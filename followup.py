"""
Automatic follow-up for conversations inactive for 3+ days.
Uses APScheduler to periodically check and send follow-up messages.
Supports WhatsApp, Facebook Messenger, and Instagram Direct.
"""
import logging
import time as _time
from datetime import datetime, timedelta

import requests
from apscheduler.schedulers.background import BackgroundScheduler

import analytics
import conversations
import whatsapp
from config import AR_TZ, NOTIFY_NUMBER, FOLLOWUP_DAYS, FOLLOWUP_ENABLED, PAGE_ACCESS_TOKEN

logger = logging.getLogger(__name__)

_scheduler = BackgroundScheduler(
    timezone=AR_TZ,
    job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 600},
)

# Track which phones have already received a follow-up (to avoid spamming)
_followed_up: set = set()

# Minimum messages to be eligible for followup
_MIN_MESSAGES = 3


def _build_followup_message(name: str = "", operation: str = "") -> str:
    """Build a personalized follow-up message based on lead data."""
    greeting = f"Hola {name}!" if name else "Hola!"
    if operation == "alquilar":
        context = "estuvimos viendo opciones de alquiler"
    elif operation == "comprar":
        context = "estuvimos hablando sobre propiedades en venta"
    else:
        context = "estuvimos en contacto por una propiedad"
    return (
        f"{greeting} Soy Vera. Te escribo porque hace unos dias {context} "
        f"y queria saber como seguis con la busqueda. "
        f"Si necesitas que te muestre nuevas opciones o coordinar una visita, escribime."
    )


def _send_meta_message(recipient_id: str, text: str) -> bool:
    """Send a message via Meta Graph API (Facebook Messenger / Instagram Direct)."""
    if not PAGE_ACCESS_TOKEN:
        logger.warning("PAGE_ACCESS_TOKEN not set — cannot send Meta followup.")
        return False
    try:
        resp = requests.post(
            "https://graph.facebook.com/v19.0/me/messages",
            params={"access_token": PAGE_ACCESS_TOKEN},
            json={
                "recipient": {"id": recipient_id},
                "message": {"text": text},
                "messaging_type": "UPDATE",
            },
            timeout=10,
        )
        if not resp.ok:
            logger.error("Meta followup send error %s: %s", resp.status_code, resp.text[:200])
            return False
        return True
    except Exception as e:
        logger.error("Failed to send Meta followup: %s", e)
        return False


def _send_followup(phone: str, channel: str, msg: str) -> bool:
    """Send followup via the appropriate channel."""
    if channel in ("facebook", "instagram"):
        return _send_meta_message(phone, msg)
    return whatsapp.send_message(phone, msg)


def _check_inactive_leads():
    """Check for conversations inactive for FOLLOWUP_DAYS+ days and send follow-up."""
    try:
        if not analytics.acquire_lock("followup", ttl_seconds=60 * 60):
            logger.info("Follow-up lock not acquired; skipping this run")
            return
        cutoff = (datetime.now(AR_TZ) - timedelta(days=FOLLOWUP_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
        recent = (datetime.now(AR_TZ) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")

        with analytics._db_lock:
            conn = analytics._get_conn()
            # Find ALL conversations with 3+ messages, inactive for FOLLOWUP_DAYS+
            # No longer requires became_lead = 1
            rows = conn.execute(
                """SELECT DISTINCT cm.phone, l.name, c.last_seen_at, l.operation, c.channel
                   FROM conversations c
                   INNER JOIN (
                       SELECT phone, phone_hash FROM chat_messages GROUP BY phone, phone_hash
                   ) cm ON cm.phone_hash = c.phone_hash
                   LEFT JOIN leads l ON cm.phone_hash = l.phone_hash
                   WHERE c.last_seen_at < ?
                     AND c.last_seen_at >= ?
                     AND c.message_count >= ?""",
                (cutoff, recent, _MIN_MESSAGES),
            ).fetchall()

        sent_count = 0
        channels_sent = {"whatsapp": 0, "facebook": 0, "instagram": 0}
        for phone, name, last_seen, operation, channel in rows:
            if phone in _followed_up:
                continue
            if analytics.has_recent_event(phone, "followup_sent", days=30):
                _followed_up.add(phone)
                continue

            if conversations.is_agent_takeover(phone):
                continue

            msg = _build_followup_message(name or "", operation or "")
            success = _send_followup(phone, channel or "whatsapp", msg)
            if success:
                _followed_up.add(phone)
                conversations.add_message(phone, "assistant", msg)
                analytics.log_event("followup_sent", phone, channel=channel or "whatsapp")
                sent_count += 1
                channels_sent[channel or "whatsapp"] = channels_sent.get(channel or "whatsapp", 0) + 1
                logger.info("Follow-up sent to %s (%s) via %s", phone[:8], name or "unnamed", channel)
            else:
                logger.warning("Failed to send follow-up to %s via %s", phone[:8], channel)

            _time.sleep(2)  # Rate limit between sends

        if sent_count > 0:
            detail = ", ".join(f"{ch}: {n}" for ch, n in channels_sent.items() if n > 0)
            whatsapp.send_message(
                NOTIFY_NUMBER,
                f"Se enviaron {sent_count} mensajes de seguimiento automatico "
                f"a conversaciones inactivas ({FOLLOWUP_DAYS}+ dias sin contacto). "
                f"Canales: {detail}"
            )
            logger.info("Follow-up batch complete: %d messages sent (%s)", sent_count, detail)

    except Exception as e:
        logger.error("Follow-up check error: %s", e, exc_info=True)


def start():
    """Start the follow-up scheduler. Call once at app startup."""
    if not FOLLOWUP_ENABLED:
        logger.info("Follow-up scheduler disabled (FOLLOWUP_ENABLED=false)")
        return
    _scheduler.add_job(
        _check_inactive_leads,
        "interval",
        hours=6,
        id="followup_check",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        "Follow-up scheduler started (every 6h, threshold: %d days, min msgs: %d)",
        FOLLOWUP_DAYS, _MIN_MESSAGES,
    )
