"""
Send WhatsApp messages via Meta's Cloud API.
"""
import logging
import os
import requests
from config import PHONE_NUMBER_ID

logger = logging.getLogger(__name__)

API_URL = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
MEDIA_URL = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/media"


def _get_token() -> str:
    """Read token fresh from env each call to catch rotation or late-loading."""
    # Try env directly first (bypasses any module-load-time caching in config.py)
    token = os.environ.get("WHATSAPP_TOKEN", "")
    if not token:
        # Fallback to whatever config imported at startup
        from config import WHATSAPP_TOKEN
        token = WHATSAPP_TOKEN
    return token


def _normalize_ar_number(phone: str) -> str:
    """Meta's API requires Argentine mobile numbers without the '9' (5411... not 5491...)."""
    if phone.startswith("549") and len(phone) == 13:
        return "54" + phone[3:]
    return phone


_DEMO_PREFIX = "54110000"  # Demo phone range seeded by analytics mock data


def _is_demo_number(phone: str) -> bool:
    """Return True if *phone* belongs to the seeded demo range."""
    normalized = _normalize_ar_number(phone)
    return normalized.startswith(_DEMO_PREFIX)


def send_message(to: str, text: str) -> bool:
    """Send a text message to a WhatsApp number. Returns True on success."""
    if _is_demo_number(to):
        logger.warning("Blocked message to demo number %s", to)
        return False
    to = _normalize_ar_number(to)
    token = _get_token()
    token_preview = token[:20] if token else "(empty)"
    logger.debug("Using WHATSAPP_TOKEN (first 20 chars): %s", token_preview)

    if not token:
        logger.error("WHATSAPP_TOKEN is empty — cannot send message to %s", to)
        return False

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }
    resp = None
    try:
        resp = requests.post(API_URL, headers=headers, json=payload, timeout=10)
        if resp.status_code == 401:
            logger.error(
                "401 Unauthorized sending to %s — token preview: %s | response: %s",
                to, token_preview, resp.text,
            )
            return False
        resp.raise_for_status()
        logger.info("Message sent to %s", to)
        return True
    except requests.HTTPError as e:
        body = resp.text if resp is not None else "(no response)"
        logger.error("WhatsApp API error sending to %s: %s — %s", to, e, body)
        return False
    except Exception as e:
        logger.error("Unexpected error sending to %s: %s", to, e)
        return False


def send_buttons(to: str, body: str, buttons: list[dict]) -> bool:
    """Send a reply-button interactive message (max 3 buttons).
    buttons: [{"id": "...", "title": "..."}, ...]
    """
    if _is_demo_number(to):
        return False
    to = _normalize_ar_number(to)
    token = _get_token()
    if not token:
        logger.error("WHATSAPP_TOKEN is empty — cannot send buttons to %s", to)
        return False
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": b["id"], "title": b["title"]}}
                    for b in buttons[:3]
                ]
            },
        },
    }
    resp = None
    try:
        resp = requests.post(API_URL, headers=headers, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("Buttons sent to %s", to)
        return True
    except Exception as e:
        body_text = resp.text if resp is not None else "(no response)"
        logger.error("WhatsApp buttons error to %s: %s — %s", to, e, body_text)
        return False


def send_list(to: str, body: str, button_text: str, sections: list[dict]) -> bool:
    """Send a list interactive message.
    sections: [{"title": "...", "rows": [{"id": "...", "title": "...", "description": "..."}]}]
    """
    if _is_demo_number(to):
        return False
    to = _normalize_ar_number(to)
    token = _get_token()
    if not token:
        logger.error("WHATSAPP_TOKEN is empty — cannot send list to %s", to)
        return False
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body},
            "action": {
                "button": button_text,
                "sections": sections,
            },
        },
    }
    resp = None
    try:
        resp = requests.post(API_URL, headers=headers, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("List sent to %s", to)
        return True
    except Exception as e:
        body_text = resp.text if resp is not None else "(no response)"
        logger.error("WhatsApp list error to %s: %s — %s", to, e, body_text)
        return False


def send_image(to: str, image_data: bytes, mime_type: str = "image/jpeg", caption: str = None) -> bool:
    """Upload an image to Meta and send it as a WhatsApp image message."""
    if _is_demo_number(to):
        logger.warning("Blocked image to demo number %s", to)
        return False
    to = _normalize_ar_number(to)
    token = _get_token()
    if not token:
        logger.error("WHATSAPP_TOKEN is empty — cannot send image to %s", to)
        return False

    headers = {"Authorization": f"Bearer {token}"}

    # Step 1 — upload media
    ext = mime_type.split("/")[-1].replace("jpeg", "jpg")
    files = {"file": (f"photo.{ext}", image_data, mime_type)}
    data = {"messaging_product": "whatsapp"}

    try:
        resp = requests.post(MEDIA_URL, headers=headers, files=files, data=data, timeout=30)
        resp.raise_for_status()
        media_id = resp.json().get("id")
        if not media_id:
            logger.error("No media_id in upload response for %s: %s", to, resp.text)
            return False
    except Exception as e:
        logger.error("Failed to upload image for %s: %s", to, e)
        return False

    # Step 2 — send image message
    image_obj: dict = {"id": media_id}
    if caption:
        image_obj["caption"] = caption

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "image",
        "image": image_obj,
    }
    try:
        resp = requests.post(
            API_URL,
            headers={**headers, "Content-Type": "application/json"},
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        logger.info("Image sent to %s (media_id: %s)", to, media_id)
        return True
    except Exception as e:
        logger.error("Failed to send image to %s: %s", to, e)
        return False
