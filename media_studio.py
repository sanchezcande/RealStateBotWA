"""
Media Studio — AI-powered content generation for property listings.
Uses Google Gemini (Veo for video, Imagen for images).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import GOOGLE_AI_API_KEY, MEDIA_UPLOAD_DIR, AR_TZ

logger = logging.getLogger(__name__)

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
# In-memory job store (lightweight; no need for DB for generation jobs)
# ---------------------------------------------------------------------------

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

BASE_VIDEO_PROMPT = (
    "Create a photorealistic real-estate video clip from the provided reference photo. "
    "The reference photo is the single source of truth. "
    "Show exactly the same room or space with the same layout, architecture, furniture, decor, materials, colors, object positions, window views, and lighting direction. "
    "Do not add, remove, replace, restyle, or reposition anything. "
    "Do not invent new rooms, angles, doors, windows, furniture, decorations, reflections, people, pets, text, logos, or architectural elements. "
    "Preserve geometry perfectly: no warping, bending, stretching, morphing, or changing proportions. "
    "Maintain frame-to-frame consistency: no flicker, flashing, exposure pulsing, color shifts, texture crawling, focus breathing, or detail instability. "
    "Use only subtle stabilized camera motion, like a very slow dolly or pan. "
    "No sudden movement, no jump cuts, no fast zoom, no whip pan, no dramatic parallax, no cinematic effects, no fake lens flares. "
    "Keep natural real-estate lighting with stable brightness and stable white balance. "
    "Output should feel like a high-end professional property video shot from the original photo, without changing the scene in any way."
)


def _get_client():
    """Lazy-init the Gemini client."""
    key = os.environ.get("GOOGLE_AI_API_KEY", "") or GOOGLE_AI_API_KEY
    if not key:
        raise RuntimeError("GOOGLE_AI_API_KEY no configurada")
    from google import genai
    return genai.Client(api_key=key)


def _clean_prompt_text(text: str) -> str:
    return " ".join((text or "").split())


def _build_video_prompt(user_prompt: str = "", property_name: str = "") -> str:
    prompt_parts = [BASE_VIDEO_PROMPT]

    property_name = _clean_prompt_text(property_name)
    if property_name:
        prompt_parts.append(
            f"The property name is '{property_name}'. Use it only as context; do not generate text overlays."
        )

    user_prompt = _clean_prompt_text(user_prompt)
    if user_prompt:
        prompt_parts.append(
            "Additional preference that must never override scene fidelity or add new elements: "
            f"{user_prompt}"
        )

    return " ".join(prompt_parts)


# ---------------------------------------------------------------------------
# Photo upload
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
# Video generation with Veo
# ---------------------------------------------------------------------------

def _generate_video_task(job_id: str, photo_paths: list[str], prompt: str,
                         property_name: str):
    """Background task that generates a video tour from photos using Veo."""
    try:
        logger.info(
            "Starting video job %s with %d photo(s) for property=%r",
            job_id, len(photo_paths), property_name,
        )
        _update_job(job_id, status="processing", progress="Conectando con Gemini...")

        client = _get_client()
        from google.genai import types

        clips = []

        if len(photo_paths) == 1:
            # Single photo: generate video from that image
            logger.info("Video job %s using single photo %s", job_id, photo_paths[0])
            _update_job(job_id, progress="Generando video desde foto...")

            with open(photo_paths[0], "rb") as f:
                image_bytes = f.read()

            first_image = types.Image(
                image_bytes=image_bytes,
                mime_type=_mime_type(photo_paths[0]),
            )

            video_prompt = _build_video_prompt(prompt, property_name)

            operation = client.models.generate_videos(
                model="veo-3.0-generate-001",
                prompt=video_prompt,
                image=first_image,
                config=types.GenerateVideosConfig(
                    aspect_ratio="16:9",
                    negative_prompt="people, pets, animals, text, logos, watermarks, new furniture, new objects, changed layout, different room, morphing, warping, flickering, color shifts",
                ),
            )

            # Poll for completion
            import time
            while not operation.done:
                time.sleep(10)
                operation = client.operations.get(operation)
                _update_job(job_id, progress="Generando video... (esto tarda 1-3 min)")

            if operation.response and operation.response.generated_videos:
                video = operation.response.generated_videos[0]
                out_path = str(UPLOAD_DIR / "videos" / f"{job_id}.mp4")
                _save_video(video, out_path)
                clips.append(out_path)
                logger.info("Video job %s saved single clip to %s", job_id, out_path)
            else:
                logger.warning("Video job %s returned no generated videos for single photo", job_id)

        else:
            # Multiple photos: generate a clip from each photo
            # Cap at 5 clips to avoid excessive API usage and cost
            max_clips = 5
            selected_paths = photo_paths[:max_clips]
            if len(photo_paths) > max_clips:
                # Evenly sample photos to cover the full set
                step = len(photo_paths) / max_clips
                selected_paths = [photo_paths[int(i * step)] for i in range(max_clips)]
                logger.info("Sampling %d of %d photos for video generation", max_clips, len(photo_paths))
            logger.info(
                "Video job %s selected %d photo(s) for clips: %s",
                job_id, len(selected_paths), selected_paths,
            )

            for i, photo_path in enumerate(selected_paths):
                clip_num = i + 1
                logger.info(
                    "Video job %s starting clip %d/%d from %s",
                    job_id, clip_num, len(selected_paths), photo_path,
                )
                _update_job(job_id,
                            progress=f"Generando clip {clip_num}/{len(selected_paths)}...")

                with open(photo_path, "rb") as f:
                    img_bytes = f.read()

                img = types.Image(
                    image_bytes=img_bytes,
                    mime_type=_mime_type(photo_path),
                )

                clip_prompt = _build_video_prompt(prompt, property_name)

                try:
                    operation = client.models.generate_videos(
                        model="veo-3.0-generate-001",
                        prompt=clip_prompt,
                        image=img,
                        config=types.GenerateVideosConfig(
                            aspect_ratio="16:9",
                            negative_prompt="people, pets, animals, text, logos, watermarks, new furniture, new objects, changed layout, different room, morphing, warping, flickering, color shifts",
                        ),
                    )

                    # Poll for completion
                    import time
                    while not operation.done:
                        time.sleep(10)
                        operation = client.operations.get(operation)
                        _update_job(job_id,
                                    progress=f"Generando clip {clip_num}/{len(selected_paths)}... (1-3 min por clip)")

                    if operation.response and operation.response.generated_videos:
                        video = operation.response.generated_videos[0]
                        clip_path = str(UPLOAD_DIR / "videos" / f"{job_id}_clip{i}.mp4")
                        _save_video(video, clip_path)
                        _trim_video(clip_path, CLIP_DURATION)
                        clips.append(clip_path)
                        logger.info(
                            "Video job %s clip %d/%d generated OK at %s",
                            job_id, clip_num, len(selected_paths), clip_path,
                        )
                    else:
                        logger.warning(
                            "Video job %s generated no video for clip %d/%d from %s",
                            job_id, clip_num, len(selected_paths), photo_path,
                        )
                except Exception as clip_err:
                    logger.error(
                        "Video job %s clip %d/%d failed for %s: %s",
                        job_id, clip_num, len(selected_paths), photo_path, clip_err,
                    )
                    _update_job(job_id,
                                progress=f"Clip {clip_num} falló, continuando...")

            if len(clips) != len(selected_paths):
                logger.warning(
                    "Video job %s produced %d/%d clips; aborting partial result",
                    job_id, len(clips), len(selected_paths),
                )
                for clip_path in clips:
                    try:
                        os.unlink(clip_path)
                    except OSError:
                        pass
                _update_job(
                    job_id,
                    status="error",
                    error=f"Solo se pudieron generar {len(clips)} de {len(selected_paths)} clips. No se guardo un video parcial.",
                )
                return

        if not clips:
            _update_job(job_id, status="error", error="No se generaron videos")
            return

        # If multiple clips, concatenate with ffmpeg
        if len(clips) > 1:
            _update_job(job_id, progress="Uniendo clips...")
            final_path = str(UPLOAD_DIR / "videos" / f"{job_id}.mp4")
            logger.info("Video job %s concatenating %d clips into %s", job_id, len(clips), final_path)
            _concat_videos(clips, final_path)
            # Clean up individual clips
            for c in clips:
                try:
                    os.unlink(c)
                except OSError:
                    pass
        else:
            final_path = clips[0]

        _update_job(
            job_id,
            status="completed",
            progress="Listo!",
            result_path=final_path,
            result_url=f"/uploads/videos/{os.path.basename(final_path)}",
        )
        logger.info("Video job %s completed successfully: %s", job_id, final_path)

    except Exception as e:
        logger.error("Video generation failed for job %s: %s", job_id, e, exc_info=True)
        _update_job(job_id, status="error", error=str(e))


def generate_video_tour(photo_paths: list[str], prompt: str = "",
                        property_name: str = "") -> str:
    """Start async video generation. Returns job_id."""
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "type": "video_tour",
        "status": "queued",
        "progress": "En cola...",
        "property": property_name,
        "photo_count": len(photo_paths),
        "prompt": _build_video_prompt(prompt, property_name),
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
        args=(job_id, photo_paths, prompt, property_name),
        daemon=True,
    )
    thread.start()
    return job_id


# ---------------------------------------------------------------------------
# Image generation with Imagen
# ---------------------------------------------------------------------------

def generate_image(prompt: str, property_name: str = "") -> str:
    """Start async image generation. Returns job_id."""
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "type": "image",
        "status": "queued",
        "progress": "En cola...",
        "property": property_name,
        "photo_count": 0,
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
        target=_generate_image_task,
        args=(job_id, prompt, property_name),
        daemon=True,
    )
    thread.start()
    return job_id


def _generate_image_task(job_id: str, prompt: str, property_name: str):
    """Background task for image generation with Imagen."""
    try:
        _update_job(job_id, status="processing", progress="Generando imagen...")

        client = _get_client()
        from google.genai import types

        response = client.models.generate_images(
            model="imagen-3.0-generate-002",
            prompt=prompt,
            config=types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio="16:9",
                output_mime_type="image/jpeg",
            ),
        )

        if response.generated_images:
            img = response.generated_images[0]
            out_path = str(UPLOAD_DIR / "photos" / f"gen_{job_id}.jpg")
            img.image.save(out_path)
            _update_job(
                job_id,
                status="completed",
                progress="Listo!",
                result_path=out_path,
                result_url=f"/uploads/photos/gen_{job_id}.jpg",
            )
        else:
            _update_job(job_id, status="error", error="No se genero imagen")

    except Exception as e:
        logger.error("Image generation failed for job %s: %s", job_id, e, exc_info=True)
        _update_job(job_id, status="error", error=str(e))


# ---------------------------------------------------------------------------
# Job management (in-memory + SQLite persistence)
# ---------------------------------------------------------------------------

def _update_job(job_id: str, **kwargs):
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(kwargs)
            # Persist to DB
            import analytics
            analytics.save_media_job(_jobs[job_id])


def get_job(job_id: str) -> Optional[dict]:
    with _jobs_lock:
        if job_id in _jobs:
            return dict(_jobs[job_id])
    # Fallback to DB (for jobs from previous deploys)
    import analytics
    return analytics.get_media_job(job_id)


def list_jobs() -> list[dict]:
    # Merge in-memory (active) jobs with DB (historical) jobs
    import analytics
    db_jobs = analytics.list_media_jobs(days=7)
    with _jobs_lock:
        # In-memory jobs take precedence (have latest status)
        merged = {j["id"]: j for j in db_jobs}
        for j in _jobs.values():
            merged[j["id"]] = j
    return sorted(merged.values(), key=lambda j: j["created_at"], reverse=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _save_video(video, out_path: str):
    """Save a generated video to disk, handling both local and remote videos."""
    import requests as _requests
    try:
        # Try direct save first (works for local/inline videos)
        video.video.save(out_path)
        logger.info("Saved generated video directly to %s", out_path)
    except Exception:
        # Fallback: download from URI with API key auth
        uri = getattr(video.video, "uri", None)
        if uri:
            key = os.environ.get("GOOGLE_AI_API_KEY", "") or GOOGLE_AI_API_KEY
            sep = "&" if "?" in uri else "?"
            auth_uri = f"{uri}{sep}key={key}"
            logger.info("Downloading generated video from remote URI to %s", out_path)
            resp = _requests.get(auth_uri, timeout=120)
            resp.raise_for_status()
            with open(out_path, "wb") as f:
                f.write(resp.content)
        else:
            raise RuntimeError("No se pudo guardar el video generado")


CLIP_DURATION = 5  # seconds per clip when combining multiple photos


def _trim_video(input_path: str, duration: int = CLIP_DURATION):
    """Trim a video to the given duration in-place using ffmpeg."""
    import subprocess
    trimmed = input_path + ".trimmed.mp4"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", input_path, "-t", str(duration),
             "-c:v", "libx264", "-preset", "fast", "-crf", "18",
             "-an", "-movflags", "+faststart", trimmed],
            check=True, capture_output=True, timeout=60,
        )
        os.replace(trimmed, input_path)
    except Exception as e:
        stderr = ""
        if hasattr(e, "stderr") and e.stderr:
            try:
                stderr = e.stderr.decode(errors="ignore")
            except Exception:
                stderr = str(e.stderr)
        logger.warning("Trim failed for %s, keeping original: %s %s", input_path, e, stderr)
        try:
            os.unlink(trimmed)
        except OSError:
            pass


def _normalize_video_for_concat(input_path: str):
    """Re-encode a clip to stable concat-friendly settings."""
    import subprocess

    normalized = input_path + ".normalized.mp4"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", input_path,
             "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,"
                    "pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p",
             "-r", "30",
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
        stderr = ""
        if hasattr(e, "stderr") and e.stderr:
            try:
                stderr = e.stderr.decode(errors="ignore")
            except Exception:
                stderr = str(e.stderr)
        logger.warning("Normalize failed for %s, keeping original clip: %s %s", input_path, e, stderr)
        try:
            os.unlink(normalized)
        except OSError:
            pass


def _mime_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
    }.get(ext, "image/jpeg")


def _concat_videos(clip_paths: list[str], output_path: str):
    """Concatenate video clips using ffmpeg with normalized encoding."""
    import subprocess

    logger.info("Preparing concat for %d clip(s) into %s", len(clip_paths), output_path)
    for clip_path in clip_paths:
        _normalize_video_for_concat(clip_path)

    # Write concat list
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
             "-r", "30",
             "-movflags", "+faststart",
             "-an",
             output_path],
            check=True, capture_output=True, timeout=120,
        )
        logger.info("Concat completed successfully into %s", output_path)
    except FileNotFoundError:
        raise RuntimeError("ffmpeg no esta instalado; no se pueden unir multiples clips")
    except subprocess.CalledProcessError as e:
        logger.error("ffmpeg concat failed for %s: %s", output_path, e.stderr.decode(errors="ignore"))
        raise RuntimeError("ffmpeg fallo al unir los clips del video")
    finally:
        try:
            os.unlink(list_path)
        except OSError:
            pass
