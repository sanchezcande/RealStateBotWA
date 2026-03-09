"""
Send WhatsApp messages via Meta's Cloud API.
"""
import logging
import os
import requests
from config import PHONE_NUMBER_ID

logger = logging.getLogger(__name__)

API_URL = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"


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


def send_message(to: str, text: str) -> bool:
    """Send a text message to a WhatsApp number. Returns True on success."""
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
        logger.error("WhatsApp API error sending to %s: %s — %s", to, e, resp.text)
        return False
    except Exception as e:
        logger.error("Unexpected error sending to %s: %s", to, e)
        return False
