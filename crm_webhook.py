"""
CRM webhook integration.
Fires HTTP POST to a configured URL on key events (lead_qualified, visit_scheduled).
"""
import hashlib
import hmac
import json
import logging
import threading
from datetime import datetime

import requests

from config import AR_TZ, CRM_WEBHOOK_URL, CRM_WEBHOOK_SECRET

logger = logging.getLogger(__name__)

_TIMEOUT = 10  # seconds


def _sign_payload(payload: str) -> str:
    """Create HMAC-SHA256 signature for webhook payload."""
    if not CRM_WEBHOOK_SECRET:
        return ""
    return hmac.new(
        CRM_WEBHOOK_SECRET.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()


def _send(event_type: str, data: dict):
    """Send webhook in a background thread (fire-and-forget)."""
    if not CRM_WEBHOOK_URL:
        return

    payload = {
        "event": event_type,
        "timestamp": datetime.now(AR_TZ).isoformat(),
        "data": data,
    }

    def _deliver():
        try:
            body = json.dumps(payload, ensure_ascii=False)
            headers = {
                "Content-Type": "application/json",
                "X-PropBot-Event": event_type,
            }
            if CRM_WEBHOOK_SECRET:
                headers["X-PropBot-Signature"] = _sign_payload(body)

            resp = requests.post(
                CRM_WEBHOOK_URL,
                data=body,
                headers=headers,
                timeout=_TIMEOUT,
            )
            logger.info("CRM webhook %s delivered: status=%d", event_type, resp.status_code)
        except Exception as e:
            logger.warning("CRM webhook %s failed: %s", event_type, e)

    threading.Thread(target=_deliver, daemon=True).start()


def on_lead_qualified(phone_hash: str, name: str = "", operation: str = "",
                      property_type: str = "", budget: str = "",
                      timeline: str = "", channel: str = "whatsapp"):
    """Fire webhook when a lead is qualified."""
    _send("lead_qualified", {
        "phone_hash": phone_hash,
        "name": name,
        "operation": operation,
        "property_type": property_type,
        "budget": budget,
        "timeline": timeline,
        "channel": channel,
    })


def on_visit_scheduled(phone_hash: str, client_name: str = "",
                       property_title: str = "", date: str = "",
                       time: str = "", channel: str = "whatsapp"):
    """Fire webhook when a visit is scheduled."""
    _send("visit_scheduled", {
        "phone_hash": phone_hash,
        "client_name": client_name,
        "property_title": property_title,
        "visit_date": date,
        "visit_time": time,
        "channel": channel,
    })
