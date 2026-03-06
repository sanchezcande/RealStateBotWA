"""
Send WhatsApp messages via Meta's Cloud API.
"""
import logging
import requests
from config import WHATSAPP_TOKEN, PHONE_NUMBER_ID

logger = logging.getLogger(__name__)

API_URL = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
HEADERS = {
    "Authorization": f"Bearer {WHATSAPP_TOKEN}",
    "Content-Type": "application/json",
}


def send_message(to: str, text: str) -> bool:
    """Send a text message to a WhatsApp number. Returns True on success."""
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }
    try:
        resp = requests.post(API_URL, headers=HEADERS, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("Message sent to %s", to)
        return True
    except requests.HTTPError as e:
        logger.error("WhatsApp API error sending to %s: %s — %s", to, e, resp.text)
        return False
    except Exception as e:
        logger.error("Unexpected error sending to %s: %s", to, e)
        return False
