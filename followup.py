"""
Automatic follow-up for leads that haven't responded in 3+ days.
Uses APScheduler to periodically check and send follow-up messages.
"""
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

import analytics
import conversations
import whatsapp
from config import AR_TZ, NOTIFY_NUMBER, FOLLOWUP_DAYS, FOLLOWUP_ENABLED

logger = logging.getLogger(__name__)

_scheduler = BackgroundScheduler(
    timezone=AR_TZ,
    job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 600},
)

# Track which phones have already received a follow-up (to avoid spamming)
_followed_up: set = set()

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


def _check_inactive_leads():
    """Check for leads that haven't had contact in FOLLOWUP_DAYS days and send follow-up."""
    try:
        if not analytics.acquire_lock("followup", ttl_seconds=60 * 60):
            logger.info("Follow-up lock not acquired; skipping this run")
            return
        cutoff = (datetime.now(AR_TZ) - timedelta(days=FOLLOWUP_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
        recent = (datetime.now(AR_TZ) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")

        with analytics._db_lock:
            conn = analytics._get_conn()
            rows = conn.execute(
                """SELECT cm.phone, l.name, c.last_seen_at, l.operation
                   FROM leads l
                   INNER JOIN conversations c ON l.phone_hash = c.phone_hash
                   INNER JOIN (
                       SELECT phone, phone_hash FROM chat_messages GROUP BY phone, phone_hash
                   ) cm ON cm.phone_hash = l.phone_hash
                   WHERE c.last_seen_at < ?
                     AND c.last_seen_at >= ?
                     AND c.became_lead = 1
                     AND c.message_count > 2""",
                (cutoff, recent),
            ).fetchall()

        sent_count = 0
        for phone, name, last_seen, operation in rows:
            if phone in _followed_up:
                continue
            if analytics.has_recent_event(phone, "followup_sent", days=30):
                _followed_up.add(phone)
                continue

            if conversations.is_agent_takeover(phone):
                continue

            msg = _build_followup_message(name or "", operation or "")
            success = whatsapp.send_message(phone, msg)
            if success:
                _followed_up.add(phone)
                conversations.add_message(phone, "assistant", msg)
                analytics.log_event("followup_sent", phone)
                sent_count += 1
                logger.info("Follow-up sent to %s (%s)", phone, name or "unnamed")
            else:
                logger.warning("Failed to send follow-up to %s", phone)

        if sent_count > 0:
            # Notify the agent about the follow-ups sent
            whatsapp.send_message(
                NOTIFY_NUMBER,
                f"Se enviaron {sent_count} mensajes de seguimiento automatico a leads inactivos ({FOLLOWUP_DAYS}+ dias sin contacto)."
            )
            logger.info("Follow-up batch complete: %d messages sent", sent_count)

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
    logger.info("Follow-up scheduler started (checking every 6 hours, threshold: %d days)", FOLLOWUP_DAYS)
