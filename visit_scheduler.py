"""
Visit scheduling logic.
Detects hidden <!--visit:{...}--> tags in AI responses,
creates Google Calendar events, notifies the agent, and schedules reminders.
"""
import re
import json
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

import analytics
import calendar_client
import conversations
import sheets
import whatsapp
from config import NOTIFY_NUMBER, AR_TZ

logger = logging.getLogger(__name__)

VISIT_TAG_RE = re.compile(r"<!--visit:(.*?)-->", re.DOTALL)
CANCEL_TAG_RE = re.compile(r"<!--cancel_visit:(.*?)-->", re.DOTALL)

_scheduler = BackgroundScheduler(
    timezone=AR_TZ,
    job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 600},
)
_scheduler.start()


def extract_all_visit_data(ai_text: str) -> list:
    """Extract all embedded visit JSONs from AI response (supports multiple tags)."""
    results = []
    for match in VISIT_TAG_RE.finditer(ai_text):
        try:
            results.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            logger.warning("Could not parse visit JSON: %s", match.group(1))
    return results


def extract_cancel_data(ai_text: str):
    """Extract cancel_visit JSON from AI response, if present."""
    match = CANCEL_TAG_RE.search(ai_text)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        logger.warning("Could not parse cancel_visit JSON: %s", match.group(1))
        return None


def clean_response(ai_text: str) -> str:
    """Remove all hidden visit/cancel tags from the text before sending to user."""
    text = VISIT_TAG_RE.sub("", ai_text)
    text = CANCEL_TAG_RE.sub("", text)
    return text.strip()


def _find_address(property_title: str) -> str:
    """Look up the address of a property by title from the current listings."""
    listings = sheets.get_listings()
    title_lower = property_title.lower()
    for p in listings:
        if p.get("titulo", "").lower() == title_lower:
            addr = str(p.get("direccion", "") or "").strip()
            return addr if addr and addr != "Consultar" else ""
    return ""


def _send_reminder(property_title: str, address: str, client_name: str, time_str: str):
    """Send the 1-hour-before reminder to the agent."""
    address_str = address or "Sin dirección cargada"
    name_str = client_name or "Sin nombre"
    msg = (
        f"Recordatorio — visita en 1 hora.\n"
        f"Propiedad: {property_title}\n"
        f"Dirección: {address_str}\n"
        f"Cliente: {name_str}\n"
        f"Hora: {time_str}"
    )
    whatsapp.send_message(NOTIFY_NUMBER, msg)


def _notify_cancellation(property_title: str, client_name: str, date_str: str, time_str: str, phone: str = ""):
    """Notify agent that a visit has been cancelled."""
    name_str = client_name or "Sin nombre"
    summary = conversations.get_conversation_summary(phone) if phone else "(sin mensajes)"
    msg = (
        f"Visita cancelada.\n"
        f"Propiedad: {property_title}\n"
        f"Cliente: {name_str}\n"
        f"Horario que tenía: {date_str} a las {time_str}\n\n"
        f"Resumen de la charla:\n{summary}"
    )
    whatsapp.send_message(NOTIFY_NUMBER, msg)


def _notify_visit(property_title: str, address: str, client_name: str, date_str: str, time_str: str, phone: str = ""):
    """Send immediate visit confirmation to the agent."""
    address_str = address or "Sin dirección cargada"
    name_str = client_name or "Sin nombre"
    summary = conversations.get_conversation_summary(phone) if phone else "(sin mensajes)"
    msg = (
        f"Visita confirmada!\n"
        f"Propiedad: {property_title}\n"
        f"Dirección: {address_str}\n"
        f"Cliente: {name_str}\n"
        f"Horario: {date_str} a las {time_str}\n\n"
        f"Resumen de la charla:\n{summary}"
    )
    success = whatsapp.send_message(NOTIFY_NUMBER, msg)
    if success:
        logger.info("Agent notified of visit: %s on %s %s", property_title, date_str, time_str)
    else:
        logger.error("Failed to notify agent of visit for %s", property_title)


def _schedule_reminder(property_title: str, address: str, client_name: str, date_str: str, time_str: str):
    """Schedule a WhatsApp reminder 1 hour before the visit."""
    try:
        visit_dt = AR_TZ.localize(datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M"))
        reminder_dt = visit_dt - timedelta(hours=1)
        now = datetime.now(AR_TZ)
        if reminder_dt <= now:
            logger.info("Visit is less than 1 hour away, skipping reminder scheduling.")
            return
        _scheduler.add_job(
            _send_reminder,
            "date",
            run_date=reminder_dt,
            args=[property_title, address, client_name, time_str],
            misfire_grace_time=300,
        )
        logger.info("Reminder scheduled for %s at %s", property_title, reminder_dt.isoformat())
    except Exception as e:
        logger.error("Failed to schedule reminder: %s", e)


def process(phone: str, ai_text: str) -> str:
    """
    Check AI response for visit/cancel tags. Handles multiple visits in a single message.
    Creates/deletes calendar events, notifies agent, schedules reminders.
    Returns cleaned text (tags removed).
    """
    all_visit_data = extract_all_visit_data(ai_text)
    cancel_data = extract_cancel_data(ai_text)
    clean_text = clean_response(ai_text)

    if not all_visit_data and not cancel_data:
        return clean_text

    lead = conversations.get_lead(phone)
    client_name = lead.get("name", "") or ""
    scheduled_visits = list(lead.get("scheduled_visits") or [])
    visit_events = dict(lead.get("visit_events") or {})

    # Handle cancellation
    if cancel_data:
        property_title = cancel_data.get("property", "")
        date_str = cancel_data.get("date", "")
        time_str = cancel_data.get("time", "")
        visit_key = f"{property_title}|{date_str}|{time_str}"
        event_id = visit_events.get(visit_key)
        if event_id:
            success = calendar_client.cancel_visit_event(event_id)
            if not success:
                logger.error("Could not delete calendar event for visit: %s", visit_key)
        else:
            logger.warning("No event_id stored for visit_key '%s' — skipping calendar delete", visit_key)

        if visit_key in scheduled_visits or visit_key in visit_events:
            scheduled_visits = [k for k in scheduled_visits if k != visit_key]
            visit_events = {k: v for k, v in visit_events.items() if k != visit_key}
            conversations.update_lead(
                phone,
                scheduled_visits=scheduled_visits,
                visit_events=visit_events,
                visit_scheduled=bool(scheduled_visits),
            )
            logger.info("Visit cancelled for %s: %s", phone, visit_key)
        else:
            logger.warning("Cancellation received for unknown visit_key '%s'", visit_key)
        analytics.log_event("visit_cancelled", phone, property=property_title)
        analytics.cancel_visit(phone, property_title, date_str, time_str)
        _notify_cancellation(property_title, client_name, date_str, time_str, phone=phone)

    # Handle new visits (supports multiple tags in same message)
    for visit_data in all_visit_data:
        property_title = visit_data.get("property", "Propiedad")
        date_str = visit_data.get("date", "")
        time_str = visit_data.get("time", "")

        if not date_str or not time_str:
            logger.warning("Incomplete visit data, skipping: %s", visit_data)
            continue

        visit_key = f"{property_title}|{date_str}|{time_str}"
        if visit_key in scheduled_visits or visit_key in visit_events:
            logger.info("Duplicate visit detected for %s, skipping: %s", phone, visit_key)
            continue

        address = _find_address(property_title)
        event_id = calendar_client.create_visit_event(
            property_title=property_title,
            date_str=date_str,
            time_str=time_str,
            client_phone=phone,
            client_name=client_name,
            address=address,
        )

        # Always track the visit and notify agent, regardless of calendar outcome
        scheduled_visits.append(visit_key)
        lead_update = dict(
            visit_scheduled=True,
            scheduled_visits=scheduled_visits,
        )
        if event_id is not None:
            visit_events[visit_key] = event_id
            lead_update["visit_events"] = visit_events
            _schedule_reminder(property_title, address, client_name, date_str, time_str)
        else:
            logger.warning("Could not create calendar event for %s / %s — visit tracked without event_id", phone, property_title)

        conversations.update_lead(phone, **lead_update)
        logger.info("Visit scheduled for %s (%s): %s %s %s", phone, client_name, property_title, date_str, time_str)
        analytics.log_event("visit_scheduled", phone, property=property_title,
                             operation=lead.get("operation"))
        analytics.save_visit(phone, property_title, address, client_name,
                             date_str, time_str, event_id=event_id)
        _notify_visit(property_title, address, client_name, date_str, time_str, phone=phone)

    # Do NOT append address to user-visible text after confirming visits.
    # The prompt explicitly forbids sharing the exact address at confirmation time.

    return clean_text
