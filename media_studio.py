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

import pytz

from config import GOOGLE_AI_API_KEY, MEDIA_UPLOAD_DIR

logger = logging.getLogger(__name__)

AR_TZ = pytz.timezone("America/Argentina/Buenos_Aires")

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


def _get_client():
    """Lazy-init the Gemini client."""
    key = os.environ.get("GOOGLE_AI_API_KEY", "") or GOOGLE_AI_API_KEY
    if not key:
        raise RuntimeError("GOOGLE_AI_API_KEY no configurada")
    from google import genai
    return genai.Client(api_key=key)


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
        _update_job(job_id, status="processing", progress="Conectando con Gemini...")

        client = _get_client()
        from google.genai import types

        clips = []
        total_pairs = max(len(photo_paths) - 1, 1)

        if len(photo_paths) == 1:
            # Single photo: generate video from that image
            _update_job(job_id, progress="Generando video desde foto...")

            with open(photo_paths[0], "rb") as f:
                image_bytes = f.read()

            first_image = types.Image(
                image_bytes=image_bytes,
                mime_type=_mime_type(photo_paths[0]),
            )

            operation = client.models.generate_videos(
                model="veo-2.0-generate-001",
                prompt=prompt or f"Cinematic real estate tour of {property_name}, slow smooth camera pan, professional lighting, 4K quality",
                image=first_image,
                config=types.GenerateVideosConfig(
                    aspect_ratio="16:9",
                    person_generation="dont_allow",
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

        else:
            # Multiple photos: generate a clip from each photo
            for i, photo_path in enumerate(photo_paths):
                clip_num = i + 1
                _update_job(job_id,
                            progress=f"Generando clip {clip_num}/{len(photo_paths)}...")

                with open(photo_path, "rb") as f:
                    img_bytes = f.read()

                img = types.Image(
                    image_bytes=img_bytes,
                    mime_type=_mime_type(photo_path),
                )

                clip_prompt = prompt or f"Smooth cinematic camera pan in a property tour of {property_name}, professional real estate video, elegant and modern"

                operation = client.models.generate_videos(
                    model="veo-2.0-generate-001",
                    prompt=clip_prompt,
                    image=img,
                    config=types.GenerateVideosConfig(
                        aspect_ratio="16:9",
                        person_generation="dont_allow",
                    ),
                )

                # Poll for completion
                import time
                while not operation.done:
                    time.sleep(10)
                    operation = client.operations.get(operation)
                    _update_job(job_id,
                                progress=f"Generando clip {clip_num}/{len(photo_paths)}... (1-3 min por clip)")

                if operation.response and operation.response.generated_videos:
                    video = operation.response.generated_videos[0]
                    clip_path = str(UPLOAD_DIR / "videos" / f"{job_id}_clip{i}.mp4")
                    _save_video(video, clip_path)
                    clips.append(clip_path)
                else:
                    logger.warning("No video generated for photo %d", clip_num)

        if not clips:
            _update_job(job_id, status="error", error="No se generaron videos")
            return

        # If multiple clips, concatenate with ffmpeg
        if len(clips) > 1:
            _update_job(job_id, progress="Uniendo clips...")
            final_path = str(UPLOAD_DIR / "videos" / f"{job_id}.mp4")
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
        logger.info("Video tour generated: %s", final_path)

    except Exception as e:
        logger.error("Video generation failed for job %s: %s", job_id, e, exc_info=True)
        _update_job(job_id, status="error", error=str(e))


def generate_video_tour(photo_paths: list[str], prompt: str = "",
                        property_name: str = "") -> str:
    """Start async video generation. Returns job_id."""
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {
            "id": job_id,
            "type": "video_tour",
            "status": "queued",
            "progress": "En cola...",
            "property": property_name,
            "photo_count": len(photo_paths),
            "created_at": datetime.now(AR_TZ).isoformat(),
            "result_path": None,
            "result_url": None,
            "error": None,
        }

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
    with _jobs_lock:
        _jobs[job_id] = {
            "id": job_id,
            "type": "image",
            "status": "queued",
            "progress": "En cola...",
            "property": property_name,
            "created_at": datetime.now(AR_TZ).isoformat(),
            "result_path": None,
            "result_url": None,
            "error": None,
        }

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
# Job management
# ---------------------------------------------------------------------------

def _update_job(job_id: str, **kwargs):
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(kwargs)


def get_job(job_id: str) -> Optional[dict]:
    with _jobs_lock:
        return dict(_jobs[job_id]) if job_id in _jobs else None


def list_jobs() -> list[dict]:
    with _jobs_lock:
        return sorted(_jobs.values(), key=lambda j: j["created_at"], reverse=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _save_video(video, out_path: str):
    """Save a generated video to disk, handling both local and remote videos."""
    import requests as _requests
    try:
        # Try direct save first (works for local/inline videos)
        video.video.save(out_path)
    except Exception:
        # Fallback: download from URI
        uri = getattr(video.video, "uri", None)
        if uri:
            resp = _requests.get(uri, timeout=120)
            resp.raise_for_status()
            with open(out_path, "wb") as f:
                f.write(resp.content)
        else:
            raise RuntimeError("No se pudo guardar el video generado")


def _mime_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
    }.get(ext, "image/jpeg")


def _concat_videos(clip_paths: list[str], output_path: str):
    """Concatenate video clips using ffmpeg."""
    import subprocess
    import tempfile

    # Write concat list
    list_path = output_path + ".list.txt"
    with open(list_path, "w") as f:
        for cp in clip_paths:
            f.write(f"file '{cp}'\n")

    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", list_path, "-c", "copy", output_path],
            check=True, capture_output=True, timeout=120,
        )
    except FileNotFoundError:
        logger.warning("ffmpeg not found, falling back to first clip only")
        import shutil
        shutil.copy2(clip_paths[0], output_path)
    except subprocess.CalledProcessError as e:
        logger.error("ffmpeg concat failed: %s", e.stderr.decode())
        import shutil
        shutil.copy2(clip_paths[0], output_path)
    finally:
        try:
            os.unlink(list_path)
        except OSError:
            pass
