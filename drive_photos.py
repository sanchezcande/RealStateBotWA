"""
Download photos from Google Drive folders and send them as images
instead of just sharing the Drive link.
"""
from __future__ import annotations

import io
import re
import json
import logging
import time
import threading
from config import GOOGLE_CREDENTIALS_JSON

logger = logging.getLogger(__name__)

# Cache: folder_id -> (timestamp, [(filename, bytes, mime_type), ...])
_photo_cache: dict = {}
_cache_lock = threading.Lock()
CACHE_TTL = 3600  # 1 hour

MAX_PHOTOS = 5  # Max images to send per message

# Regex patterns for Google Drive URLs
DRIVE_FOLDER_RE = re.compile(
    r'https?://drive\.google\.com/drive/folders/([a-zA-Z0-9_-]+)(?:\?[^\s)>\]]*)?'
)
DRIVE_FILE_RE = re.compile(
    r'https?://drive\.google\.com/file/d/([a-zA-Z0-9_-]+)'
)
DRIVE_OPEN_RE = re.compile(
    r'https?://drive\.google\.com/open\?id=([a-zA-Z0-9_-]+)'
)
# Matches any Drive URL (for stripping from text)
DRIVE_URL_RE = re.compile(r'https?://drive\.google\.com/[^\s)>\]]*')


def _get_drive_service():
    """Create a Google Drive API service using the service account credentials."""
    if not GOOGLE_CREDENTIALS_JSON:
        return None
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build

        creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        creds = Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
        return build("drive", "v3", credentials=creds)
    except Exception as e:
        logger.error("Failed to create Drive service: %s", e)
        return None


def extract_drive_urls(text: str) -> list[dict]:
    """Extract Google Drive URLs from text.
    Returns list of {"type": "folder"|"file", "id": str, "url": str}.
    """
    results = []
    seen_ids = set()
    for m in DRIVE_FOLDER_RE.finditer(text):
        fid = m.group(1)
        if fid not in seen_ids:
            results.append({"type": "folder", "id": fid, "url": m.group(0)})
            seen_ids.add(fid)
    for m in DRIVE_FILE_RE.finditer(text):
        fid = m.group(1)
        if fid not in seen_ids:
            results.append({"type": "file", "id": fid, "url": m.group(0)})
            seen_ids.add(fid)
    for m in DRIVE_OPEN_RE.finditer(text):
        fid = m.group(1)
        if fid not in seen_ids:
            results.append({"type": "file", "id": fid, "url": m.group(0)})
            seen_ids.add(fid)
    return results


def strip_drive_urls(text: str) -> str:
    """Remove Google Drive URLs from text and clean up whitespace."""
    cleaned = DRIVE_URL_RE.sub("", text)
    cleaned = re.sub(r"  +", " ", cleaned)
    cleaned = re.sub(r"\n\s*\n\s*\n", "\n\n", cleaned)
    return cleaned.strip()


def download_photos(drive_urls: list[dict], max_photos: int = MAX_PHOTOS) -> list[tuple]:
    """Download photos from Drive URLs.
    Returns list of (filename, image_bytes, mime_type) tuples.
    Falls back to empty list if anything fails (caller should send URL as text).
    """
    service = _get_drive_service()
    if not service:
        logger.warning("No Drive service — cannot download photos")
        return []

    all_photos: list[tuple] = []
    for url_info in drive_urls:
        if len(all_photos) >= max_photos:
            break
        try:
            if url_info["type"] == "folder":
                photos = _download_folder(service, url_info["id"], max_photos - len(all_photos))
                all_photos.extend(photos)
            else:
                photo = _download_file(service, url_info["id"])
                if photo:
                    all_photos.append(photo)
        except Exception as e:
            logger.error("Error downloading from Drive %s: %s", url_info["url"], e)

    return all_photos[:max_photos]


def _download_folder(service, folder_id: str, limit: int) -> list[tuple]:
    """Download image files from a Drive folder."""
    with _cache_lock:
        cached = _photo_cache.get(folder_id)
        if cached and (time.time() - cached[0]) < CACHE_TTL:
            logger.info("Drive folder %s served from cache (%d photos)", folder_id, len(cached[1]))
            return cached[1][:limit]

    try:
        results = service.files().list(
            q=f"'{folder_id}' in parents and (mimeType contains 'image/')",
            fields="files(id, name, mimeType)",
            orderBy="name",
            pageSize=limit,
        ).execute()

        files = results.get("files", [])
        if not files:
            logger.info("No images found in Drive folder %s", folder_id)
            return []

        photos = []
        for f in files[:limit]:
            photo = _download_file(
                service, f["id"], f.get("name", "photo.jpg"), f.get("mimeType", "image/jpeg")
            )
            if photo:
                photos.append(photo)

        with _cache_lock:
            _photo_cache[folder_id] = (time.time(), photos)

        logger.info("Downloaded %d photos from Drive folder %s", len(photos), folder_id)
        return photos
    except Exception as e:
        logger.error("Error listing Drive folder %s: %s", folder_id, e)
        return []


def _download_file(service, file_id: str, name: str = None, mime_type: str = None) -> tuple | None:
    """Download a single image file from Drive."""
    try:
        if not name or not mime_type:
            meta = service.files().get(fileId=file_id, fields="name, mimeType").execute()
            name = meta.get("name", "photo.jpg")
            mime_type = meta.get("mimeType", "image/jpeg")

        if not mime_type.startswith("image/"):
            logger.info("Skipping non-image file %s (%s)", name, mime_type)
            return None

        from googleapiclient.http import MediaIoBaseDownload

        request = service.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)

        done = False
        while not done:
            _, done = downloader.next_chunk()

        data = buf.getvalue()
        if len(data) > 5 * 1024 * 1024:  # Skip files > 5 MB
            logger.warning("Skipping %s — too large (%d bytes)", name, len(data))
            return None

        return (name, data, mime_type)
    except Exception as e:
        logger.error("Error downloading Drive file %s: %s", file_id, e)
        return None
