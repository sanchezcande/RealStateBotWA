"""
Media Studio — FFmpeg + Ken Burns video generation for property listings.
Optional Real-ESRGAN upscaling. Zero API costs.

For the original Gemini-powered version, see media_studio_gemini.py
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import random
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import MEDIA_UPLOAD_DIR, AR_TZ

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_VIDEO_FORMAT = "vertical"
VIDEO_FORMATS = {
    "vertical": {"width": 720, "height": 1280},
    "horizontal": {"width": 1280, "height": 720},
}

CLIP_DURATION = 5       # seconds per photo
CLIP_FPS = 30
FADE_DURATION = 0.4     # seconds for fade in/out on each clip
XFADE_DURATION = 0.8    # seconds for crossfade between clips

# Optional paths (set via env vars)
MUSIC_PATH = os.environ.get("MEDIA_MUSIC_PATH", "")
LOGO_PATH = os.environ.get("MEDIA_LOGO_PATH", "")
REALESRGAN_PATH = os.environ.get("REALESRGAN_PATH", "")
VOICEOVER_VOICE = os.environ.get("MEDIA_VOICEOVER_VOICE", "es-AR-TomasNeural")

# ---------------------------------------------------------------------------
# Upload directory setup
# ---------------------------------------------------------------------------

UPLOAD_DIR = Path(MEDIA_UPLOAD_DIR)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
(UPLOAD_DIR / "photos").mkdir(exist_ok=True)
(UPLOAD_DIR / "videos").mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_PHOTO_SIZE = 10 * 1024 * 1024  # 10 MB

# ---------------------------------------------------------------------------
# In-memory job store (lightweight; persisted to SQLite via analytics)
# ---------------------------------------------------------------------------

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Ken Burns effects
# ---------------------------------------------------------------------------

KENBURNS_EFFECTS = ["zoom_in", "zoom_out", "pan_lr", "pan_rl", "pan_tb", "pan_bt"]


def _kenburns_filter(effect: str, w: int, h: int) -> str:
    """Build a zoompan filter string for a specific Ken Burns effect."""
    d = CLIP_DURATION * CLIP_FPS  # total frames
    effects = {
        "zoom_in": (
            f"zoompan=z='min(zoom+0.0015,1.3)'"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={d}:s={w}x{h}:fps={CLIP_FPS}"
        ),
        "zoom_out": (
            f"zoompan=z='if(eq(on,0),1.3,max(zoom-0.0015,1.0))'"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={d}:s={w}x{h}:fps={CLIP_FPS}"
        ),
        "pan_lr": (
            f"zoompan=z='1.15'"
            f":x='if(eq(on,0),0,min(x+(iw-iw/zoom)/{d},iw-iw/zoom))'"
            f":y='ih/2-(ih/zoom/2)'"
            f":d={d}:s={w}x{h}:fps={CLIP_FPS}"
        ),
        "pan_rl": (
            f"zoompan=z='1.15'"
            f":x='if(eq(on,0),iw-iw/zoom,max(x-(iw-iw/zoom)/{d},0))'"
            f":y='ih/2-(ih/zoom/2)'"
            f":d={d}:s={w}x{h}:fps={CLIP_FPS}"
        ),
        "pan_tb": (
            f"zoompan=z='1.15'"
            f":x='iw/2-(iw/zoom/2)'"
            f":y='if(eq(on,0),0,min(y+(ih-ih/zoom)/{d},ih-ih/zoom))'"
            f":d={d}:s={w}x{h}:fps={CLIP_FPS}"
        ),
        "pan_bt": (
            f"zoompan=z='1.15'"
            f":x='iw/2-(iw/zoom/2)'"
            f":y='if(eq(on,0),ih-ih/zoom,max(y-(ih-ih/zoom)/{d},0))'"
            f":d={d}:s={w}x{h}:fps={CLIP_FPS}"
        ),
    }
    return effects.get(effect, effects["zoom_in"])


def _pick_effects(n: int) -> list[str]:
    """Pick n Ken Burns effects, avoiding consecutive repeats."""
    effects = []
    for _ in range(n):
        if effects:
            candidates = [e for e in KENBURNS_EFFECTS if e != effects[-1]]
        else:
            candidates = list(KENBURNS_EFFECTS)
        effects.append(random.choice(candidates))
    return effects


# ---------------------------------------------------------------------------
# Video format helpers (kept compatible with old API)
# ---------------------------------------------------------------------------

def _get_video_format_config(video_format: str | None) -> dict:
    return VIDEO_FORMATS.get((video_format or "").lower(), VIDEO_FORMATS[DEFAULT_VIDEO_FORMAT])


def _build_video_filter(video_format: str = DEFAULT_VIDEO_FORMAT, duration: float | None = None) -> str:
    """Build a scale+crop+fade filter for final polish pass."""
    video_cfg = _get_video_format_config(video_format)
    filter_parts = [
        f"scale={video_cfg['width']}:{video_cfg['height']}:force_original_aspect_ratio=increase",
        f"crop={video_cfg['width']}:{video_cfg['height']}",
        f"fps={CLIP_FPS}",
        "format=yuv420p",
    ]
    if duration and duration > 1:
        fade_in = min(0.4, duration / 3)
        fade_out = min(0.6, duration / 3)
        fade_out_start = max(duration - fade_out, fade_in)
        filter_parts.append(f"fade=t=in:st=0:d={fade_in:.2f}")
        filter_parts.append(f"fade=t=out:st={fade_out_start:.2f}:d={fade_out:.2f}")
    return ",".join(filter_parts)


# ---------------------------------------------------------------------------
# Real-ESRGAN upscaling (optional)
# ---------------------------------------------------------------------------

def _upscale_photo(input_path: str, output_path: str) -> bool:
    """Upscale a photo using Real-ESRGAN. Returns True on success."""
    binary = REALESRGAN_PATH or "realesrgan-ncnn-vulkan"
    try:
        subprocess.run(
            [binary, "-i", input_path, "-o", output_path,
             "-n", "realesrgan-x4plus", "-s", "2"],
            check=True, capture_output=True, timeout=120,
        )
        logger.info("Real-ESRGAN upscaled %s -> %s", input_path, output_path)
        return True
    except FileNotFoundError:
        logger.debug("Real-ESRGAN binary not found, skipping upscale")
        return False
    except Exception as e:
        logger.warning("Real-ESRGAN failed for %s: %s", input_path, e)
        return False


def _enhance_photo(input_path: str, output_path: str) -> str:
    """Try Real-ESRGAN, fall back to Pillow enhancement, or return original."""
    if _upscale_photo(input_path, output_path):
        return output_path
    try:
        from PIL import Image, ImageEnhance, ImageFilter
        img = Image.open(input_path)
        img = ImageEnhance.Sharpness(img).enhance(1.3)
        img = ImageEnhance.Contrast(img).enhance(1.1)
        img.save(output_path, quality=95)
        logger.info("Pillow-enhanced %s -> %s", input_path, output_path)
        return output_path
    except ImportError:
        logger.debug("Pillow not installed, using original photo")
        return input_path
    except Exception as e:
        logger.warning("Pillow enhancement failed for %s: %s", input_path, e)
        return input_path


# ---------------------------------------------------------------------------
# Ken Burns clip generation
# ---------------------------------------------------------------------------

def _generate_clip(photo_path: str, clip_path: str, effect: str,
                   video_format: str = DEFAULT_VIDEO_FORMAT) -> bool:
    """Generate a single Ken Burns clip from a photo using FFmpeg."""
    cfg = _get_video_format_config(video_format)
    w, h = cfg["width"], cfg["height"]

    zoompan = _kenburns_filter(effect, w, h)
    fade_out_start = max(CLIP_DURATION - FADE_DURATION, FADE_DURATION)
    vf = (
        f"{zoompan},"
        f"fade=t=in:st=0:d={FADE_DURATION:.2f},"
        f"fade=t=out:st={fade_out_start:.2f}:d={FADE_DURATION:.2f}"
    )

    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-loop", "1", "-i", photo_path,
                "-vf", vf,
                "-t", str(CLIP_DURATION),
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                "-an",
                clip_path,
            ],
            check=True, capture_output=True, timeout=120,
        )
        return True
    except FileNotFoundError:
        raise RuntimeError("ffmpeg no esta instalado")
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode(errors="ignore") if e.stderr else ""
        logger.error("FFmpeg clip generation failed: %s %s", e, stderr)
        return False


# ---------------------------------------------------------------------------
# Concat clips with crossfade
# ---------------------------------------------------------------------------

def _normalize_video_for_concat(input_path: str, video_format: str = DEFAULT_VIDEO_FORMAT):
    """Re-encode a clip to stable concat-friendly settings."""
    normalized = input_path + ".normalized.mp4"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", input_path,
             "-vf", _build_video_filter(video_format),
             "-r", str(CLIP_FPS),
             "-c:v", "libx264",
             "-preset", "medium",
             "-crf", "18",
             "-an",
             "-movflags", "+faststart",
             normalized],
            check=True, capture_output=True, timeout=120,
        )
        os.replace(normalized, input_path)
    except Exception as e:
        logger.warning("Normalize failed for %s: %s", input_path, e)
        try:
            os.unlink(normalized)
        except OSError:
            pass


def _concat_videos(clip_paths: list[str], output_path: str, video_format: str = DEFAULT_VIDEO_FORMAT):
    """Concatenate video clips using ffmpeg concat demuxer with re-encoding."""
    logger.info("Preparing concat for %d clip(s) into %s", len(clip_paths), output_path)
    for clip_path in clip_paths:
        _normalize_video_for_concat(clip_path, video_format=video_format)

    list_path = output_path + ".list.txt"
    with open(list_path, "w") as f:
        for cp in clip_paths:
            abs_path = str(Path(cp).resolve())
            safe_path = abs_path.replace("'", "'\\''")
            f.write(f"file '{safe_path}'\n")

    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", list_path,
             "-c:v", "libx264",
             "-preset", "medium",
             "-crf", "18",
             "-pix_fmt", "yuv420p",
             "-r", str(CLIP_FPS),
             "-movflags", "+faststart",
             "-an",
             output_path],
            check=True, capture_output=True, timeout=120,
        )
        logger.info("Concat completed successfully into %s", output_path)
    except FileNotFoundError:
        raise RuntimeError("ffmpeg no esta instalado; no se pueden unir multiples clips")
    except subprocess.CalledProcessError as e:
        logger.error("ffmpeg concat failed: %s", e.stderr.decode(errors="ignore") if e.stderr else e)
        raise RuntimeError("ffmpeg fallo al unir los clips del video")
    finally:
        try:
            os.unlink(list_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Final polish: text overlay + logo + music
# ---------------------------------------------------------------------------

_drawtext_available: bool | None = None


def _has_drawtext() -> bool:
    """Check if ffmpeg was built with the drawtext filter."""
    global _drawtext_available
    if _drawtext_available is not None:
        return _drawtext_available
    try:
        result = subprocess.run(
            ["ffmpeg", "-filters"],
            capture_output=True, text=True, timeout=10,
        )
        _drawtext_available = "drawtext" in (result.stdout or "")
    except Exception:
        _drawtext_available = False
    if not _drawtext_available:
        logger.info("ffmpeg drawtext filter not available, text overlays disabled")
    return _drawtext_available


def _find_system_font() -> str:
    """Find a usable TrueType font on the system."""
    candidates = [
        # macOS
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNSText.ttf",
        "/Library/Fonts/Arial.ttf",
        # Linux (Railway/Docker)
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return ""


def _get_video_duration(input_path: str) -> float | None:
    """Return video duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                input_path,
            ],
            check=True, capture_output=True, timeout=30,
            text=True,
        )
        return float((result.stdout or "").strip())
    except Exception as e:
        logger.warning("Could not read duration for %s: %s", input_path, e)
        return None


def _polish_final_video(input_path: str, video_format: str = DEFAULT_VIDEO_FORMAT,
                        property_name: str = "", text_overlay: str = "",
                        voiceover_path: str = ""):
    """Apply final framing, optional text overlay, logo, voiceover, and music."""
    duration = _get_video_duration(input_path)
    polished = input_path + ".polished.mp4"

    cfg = _get_video_format_config(video_format)
    w, h = cfg["width"], cfg["height"]

    # Build filter chain
    filters = [_build_video_filter(video_format, duration=duration)]

    # Text overlay (property name or custom text) — try with drawtext, fall back without
    overlay_text = text_overlay or property_name
    text_filter = ""
    if overlay_text and _has_drawtext():
        safe_text = overlay_text.replace("'", "\\'").replace(":", "\\:")
        font_path = _find_system_font()
        font_clause = f":fontfile='{font_path}'" if font_path else ""
        text_filter = (
            f",drawtext=text='{safe_text}'"
            f"{font_clause}"
            f":fontsize={int(w * 0.04)}"
            f":fontcolor=white"
            f":x=(w-text_w)/2:y=h-{int(h * 0.08)}"
            f":box=1:boxcolor=black@0.5:boxborderw=8"
        )

    vf = ",".join(filters) + text_filter

    # Build command
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", vf,
        "-r", str(CLIP_FPS),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-an", "-movflags", "+faststart",
        polished,
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=180)
        os.replace(polished, input_path)
    except Exception as e:
        logger.warning("Final polish failed for %s: %s", input_path, e)
        try:
            os.unlink(polished)
        except OSError:
            pass

    # Logo overlay (separate pass for simplicity)
    logo = LOGO_PATH
    if logo and os.path.isfile(logo):
        _apply_logo(input_path, logo)

    # Audio: voiceover (+ optional background music), or music only
    music = MUSIC_PATH
    has_music = music and os.path.isfile(music)
    has_voiceover = voiceover_path and os.path.isfile(voiceover_path)

    if has_voiceover:
        _apply_voiceover(input_path, voiceover_path, music_path=music if has_music else "")
    elif has_music:
        _apply_music(input_path, music)


def _apply_logo(video_path: str, logo_path: str):
    """Overlay a logo on the top-right corner of the video."""
    output = video_path + ".logo.mp4"
    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", video_path, "-i", logo_path,
                "-filter_complex",
                "[1:v]scale=iw*0.15:-1[logo];[0:v][logo]overlay=W-w-20:20",
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-an", "-movflags", "+faststart",
                output,
            ],
            check=True, capture_output=True, timeout=120,
        )
        os.replace(output, video_path)
        logger.info("Logo applied to %s", video_path)
    except Exception as e:
        logger.warning("Logo overlay failed: %s", e)
        try:
            os.unlink(output)
        except OSError:
            pass


def _apply_music(video_path: str, music_path: str):
    """Add background music to the video, fading out at the end."""
    duration = _get_video_duration(video_path) or 10
    output = video_path + ".music.mp4"
    fade_start = max(duration - 2, 0)
    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", video_path, "-i", music_path,
                "-filter_complex",
                f"[1:a]atrim=0:{duration},afade=t=in:st=0:d=1,afade=t=out:st={fade_start:.1f}:d=2,volume=0.3[a]",
                "-map", "0:v", "-map", "[a]",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "128k",
                "-shortest",
                "-movflags", "+faststart",
                output,
            ],
            check=True, capture_output=True, timeout=120,
        )
        os.replace(output, video_path)
        logger.info("Music applied to %s", video_path)
    except Exception as e:
        logger.warning("Music overlay failed: %s", e)
        try:
            os.unlink(output)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Voiceover with Edge TTS (free, no API key)
# ---------------------------------------------------------------------------

def _generate_voiceover(text: str, output_path: str, voice: str = "") -> bool:
    """Generate a voiceover MP3 using edge-tts. Returns True on success."""
    if not text or not text.strip():
        return False
    try:
        import edge_tts
    except ImportError:
        logger.warning("edge-tts not installed, skipping voiceover (pip install edge-tts)")
        return False
    try:
        voice = voice or VOICEOVER_VOICE
        communicate = edge_tts.Communicate(text.strip(), voice)
        asyncio.run(communicate.save(output_path))
        logger.info("Voiceover generated: %s (%d chars, voice=%s)", output_path, len(text), voice)
        return True
    except Exception as e:
        logger.error("Voiceover generation failed: %s", e)
        return False


def _apply_voiceover(video_path: str, voiceover_path: str, music_path: str = ""):
    """Mix voiceover (and optional background music) into the video."""
    duration = _get_video_duration(video_path) or 10
    output = video_path + ".voiced.mp4"
    fade_start = max(duration - 1.5, 0)

    if music_path and os.path.isfile(music_path):
        # Both voiceover + music: voice loud, music soft background
        music_fade_start = max(duration - 2, 0)
        filter_complex = (
            f"[1:a]afade=t=in:st=0:d=0.3,afade=t=out:st={fade_start:.1f}:d=1.5,volume=1.0[vo];"
            f"[2:a]atrim=0:{duration},afade=t=in:st=0:d=1,afade=t=out:st={music_fade_start:.1f}:d=2,volume=0.12[mu];"
            f"[vo][mu]amix=inputs=2:duration=first[a]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path, "-i", voiceover_path, "-i", music_path,
            "-filter_complex", filter_complex,
            "-map", "0:v", "-map", "[a]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            "-movflags", "+faststart",
            output,
        ]
    else:
        # Voiceover only
        filter_complex = (
            f"[1:a]afade=t=in:st=0:d=0.3,afade=t=out:st={fade_start:.1f}:d=1.5,volume=1.0[a]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path, "-i", voiceover_path,
            "-filter_complex", filter_complex,
            "-map", "0:v", "-map", "[a]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            "-movflags", "+faststart",
            output,
        ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        os.replace(output, video_path)
        logger.info("Voiceover applied to %s (with_music=%s)", video_path, bool(music_path))
    except Exception as e:
        logger.warning("Voiceover mixing failed: %s", e)
        try:
            os.unlink(output)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Progress messages
# ---------------------------------------------------------------------------

def _video_progress_message(step: str, current: int = 0, total: int = 0) -> str:
    if step == "enhancing":
        return f"Mejorando fotos... ({current}/{total})"
    if step == "generating":
        if total > 1:
            progress = max(min(math.floor((current / total) * 100), 95), 5)
            return f"Generando video... {progress}% completado."
        return "Generando video..."
    if step == "concatenating":
        return "Uniendo clips..."
    if step == "voiceover":
        return "Generando voz en off..."
    if step == "finishing":
        return "Aplicando acabado final..."
    return "Procesando video..."


# ---------------------------------------------------------------------------
# Photo upload (same API as before)
# ---------------------------------------------------------------------------

def save_photo(file_storage, property_name: str = "") -> dict:
    """Save an uploaded photo. Returns metadata dict."""
    filename = file_storage.filename or "photo.jpg"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Formato no soportado: {ext}. Usa JPG, PNG o WebP.")

    photo_id = uuid.uuid4().hex[:12]
    safe_name = f"{photo_id}{ext}"
    path = UPLOAD_DIR / "photos" / safe_name
    file_storage.save(str(path))

    size = path.stat().st_size
    if size > MAX_PHOTO_SIZE:
        path.unlink()
        raise ValueError("Foto demasiado grande (max 10 MB)")

    return {
        "id": photo_id,
        "filename": safe_name,
        "original_name": filename,
        "path": str(path),
        "size": size,
        "property": property_name,
        "uploaded_at": datetime.now(AR_TZ).isoformat(),
    }


def list_photos() -> list[dict]:
    """List all uploaded photos."""
    photos = []
    photo_dir = UPLOAD_DIR / "photos"
    for f in sorted(photo_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if f.suffix.lower() in ALLOWED_EXTENSIONS:
            photos.append({
                "id": f.stem,
                "filename": f.name,
                "path": str(f),
                "size": f.stat().st_size,
                "url": f"/uploads/photos/{f.name}",
            })
    return photos


def delete_photo(photo_id: str) -> bool:
    """Delete a photo by ID."""
    photo_dir = UPLOAD_DIR / "photos"
    for f in photo_dir.iterdir():
        if f.stem == photo_id:
            f.unlink()
            return True
    return False


# ---------------------------------------------------------------------------
# Video generation task (background thread)
# ---------------------------------------------------------------------------

def _generate_video_task(job_id: str, photo_paths: list[str], prompt: str,
                         property_name: str, video_format: str = DEFAULT_VIDEO_FORMAT,
                         voice: str = ""):
    """Background task: Ken Burns slideshow from photos using FFmpeg."""
    try:
        cfg = _get_video_format_config(video_format)
        logger.info(
            "Starting FFmpeg video job %s with %d photo(s) format=%s",
            job_id, len(photo_paths), video_format,
        )

        # Step 1: Enhance photos (optional Real-ESRGAN / Pillow)
        _update_job(job_id, status="processing",
                    progress=_video_progress_message("enhancing", 0, len(photo_paths)))

        enhanced_paths = []
        temp_enhanced = []
        for i, photo_path in enumerate(photo_paths):
            _update_job(job_id, progress=_video_progress_message("enhancing", i + 1, len(photo_paths)))
            enhanced_path = str(UPLOAD_DIR / "videos" / f"{job_id}_enhanced_{i}.png")
            result = _enhance_photo(photo_path, enhanced_path)
            enhanced_paths.append(result)
            if result == enhanced_path:
                temp_enhanced.append(enhanced_path)

        # Step 2: Generate Ken Burns clips
        effects = _pick_effects(len(enhanced_paths))
        clips = []
        for i, (photo_path, effect) in enumerate(zip(enhanced_paths, effects)):
            _update_job(job_id, progress=_video_progress_message("generating", i + 1, len(enhanced_paths)))
            clip_path = str(UPLOAD_DIR / "videos" / f"{job_id}_clip{i}.mp4")

            if _generate_clip(photo_path, clip_path, effect, video_format):
                clips.append(clip_path)
                logger.info("Job %s clip %d/%d generated: %s (%s)",
                            job_id, i + 1, len(enhanced_paths), clip_path, effect)
            else:
                logger.warning("Job %s clip %d/%d failed", job_id, i + 1, len(enhanced_paths))

        # Clean up enhanced temp files
        for tf in temp_enhanced:
            try:
                os.unlink(tf)
            except OSError:
                pass

        if not clips:
            _update_job(job_id, status="error", error="No se pudieron generar clips de video")
            return

        if len(clips) != len(enhanced_paths):
            # Clean up partial clips
            for c in clips:
                try:
                    os.unlink(c)
                except OSError:
                    pass
            _update_job(
                job_id, status="error",
                error=f"Solo se generaron {len(clips)} de {len(enhanced_paths)} clips. No se guardo un video parcial.",
            )
            return

        # Step 3: Concatenate
        final_path = str(UPLOAD_DIR / "videos" / f"{job_id}.mp4")
        if len(clips) > 1:
            _update_job(job_id, progress=_video_progress_message("concatenating"))
            _concat_videos(clips, final_path, video_format=video_format)
            for c in clips:
                try:
                    os.unlink(c)
                except OSError:
                    pass
        else:
            os.replace(clips[0], final_path)

        # Step 4: Generate voiceover (if prompt text provided)
        voiceover_path = ""
        voiceover_text = prompt.strip() if prompt else ""
        if voiceover_text:
            _update_job(job_id, progress=_video_progress_message("voiceover"))
            vo_path = str(UPLOAD_DIR / "videos" / f"{job_id}_voiceover.mp3")
            if _generate_voiceover(voiceover_text, vo_path, voice=voice):
                voiceover_path = vo_path

        # Step 5: Final polish (text, logo, voiceover, music)
        _update_job(job_id, progress=_video_progress_message("finishing"))
        text_overlay = property_name  # text overlay = property name (prompt goes to voiceover)
        _polish_final_video(final_path, video_format=video_format,
                            property_name=property_name, text_overlay=text_overlay,
                            voiceover_path=voiceover_path)

        # Clean up voiceover temp
        if voiceover_path:
            try:
                os.unlink(voiceover_path)
            except OSError:
                pass

        _update_job(
            job_id,
            status="completed",
            progress="Listo!",
            result_path=final_path,
            result_url=f"/uploads/videos/{os.path.basename(final_path)}",
        )
        logger.info("Video job %s completed: %s", job_id, final_path)

    except Exception as e:
        logger.error("Video generation failed for job %s: %s", job_id, e, exc_info=True)
        _update_job(job_id, status="error", error=str(e))


def generate_video_tour(photo_paths: list[str], prompt: str = "",
                        property_name: str = "", video_format: str = DEFAULT_VIDEO_FORMAT,
                        voice: str = "") -> str:
    """Start async video generation. Returns job_id."""
    job_id = uuid.uuid4().hex[:12]
    normalized_format = (video_format or DEFAULT_VIDEO_FORMAT).lower()
    job = {
        "id": job_id,
        "type": "video_tour",
        "status": "queued",
        "progress": "En cola...",
        "property": property_name,
        "photo_count": len(photo_paths),
        "video_format": normalized_format,
        "prompt": prompt,
        "created_at": datetime.now(AR_TZ).isoformat(),
        "result_path": None,
        "result_url": None,
        "error": None,
    }
    with _jobs_lock:
        _jobs[job_id] = job
    import analytics
    analytics.save_media_job(job)

    thread = threading.Thread(
        target=_generate_video_task,
        args=(job_id, photo_paths, prompt, property_name, normalized_format, voice),
        daemon=True,
    )
    thread.start()
    return job_id


# ---------------------------------------------------------------------------
# Image generation (stub — requires Gemini backend)
# ---------------------------------------------------------------------------

def generate_image(prompt: str, property_name: str = "") -> str:
    """Image generation from prompts requires the Gemini backend.
    See media_studio_gemini.py to re-enable AI image generation."""
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "type": "image",
        "status": "error",
        "progress": "",
        "property": property_name,
        "photo_count": 0,
        "prompt": prompt,
        "created_at": datetime.now(AR_TZ).isoformat(),
        "result_path": None,
        "result_url": None,
        "error": "Generacion de imagenes desde texto no disponible con el backend FFmpeg. Usa media_studio_gemini para esta funcion.",
    }
    with _jobs_lock:
        _jobs[job_id] = job
    import analytics
    analytics.save_media_job(job)
    return job_id


# ---------------------------------------------------------------------------
# Job management (in-memory + SQLite persistence)
# ---------------------------------------------------------------------------

def _update_job(job_id: str, **kwargs):
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(kwargs)
            import analytics
            analytics.save_media_job(_jobs[job_id])


def get_job(job_id: str) -> Optional[dict]:
    with _jobs_lock:
        if job_id in _jobs:
            return dict(_jobs[job_id])
    import analytics
    return analytics.get_media_job(job_id)


def list_jobs() -> list[dict]:
    import analytics
    db_jobs = analytics.list_media_jobs(days=7)
    with _jobs_lock:
        merged = {j["id"]: j for j in db_jobs}
        for j in _jobs.values():
            merged[j["id"]] = j
    return sorted(merged.values(), key=lambda j: j["created_at"], reverse=True)
