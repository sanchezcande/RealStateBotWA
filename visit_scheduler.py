"""
Visit scheduling logic.
Detects hidden <!--visit:{...}--> tags in AI responses,
creates Google Calendar events, notifies the agent, and schedules reminders.
"""
import re
import json
import logging
from datetime import datetime, timedelta

import pytz
from apscheduler.schedulers.background import BackgroundScheduler

import calendar_client
import conversations
import sheets
import whatsapp
from config import NOTIFY_NUMBER

logger = logging.getLogger(__name__)

AR_TZ = pytz.timezone("America/Argentina/Buenos_Aires")

VISIT_TAG_RE = re.compile(r"<!--visit:(.*?)-->", re.DOTALL)

_scheduler = BackgroundScheduler(timezone=AR_TZ)
_scheduler.start()


def extract_visit_data(ai_text: str):
    """Extract embedded visit JSON from AI response, if present."""
    match = VISIT_TAG_RE.search(ai_text)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        logger.warning("Could not parse visit JSON: %s", match.group(1))
        return None


def clean_response(ai_text: str) -> str:
    """Remove the hidden visit tag from the text before sending to user."""
    return VISIT_TAG_RE.sub("", ai_text).strip()


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


def _notify_visit(property_title: str, address: str, client_name: str, date_str: str, time_str: str):
    """Send immediate visit confirmation to the agent."""
    address_str = address or "Sin dirección cargada"
    name_str = client_name or "Sin nombre"
    msg = (
        f"Visita confirmada!\n"
        f"Propiedad: {property_title}\n"
        f"Dirección: {address_str}\n"
        f"Cliente: {name_str}\n"
        f"Horario: {date_str} a las {time_str}"
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
    Check AI response for a visit tag. If found, create calendar event,
    notify agent, and schedule reminder.
    Returns cleaned text (tag removed).
    """
    visit_data = extract_visit_data(ai_text)
    clean_text = clean_response(ai_text)

    if not visit_data:
        return clean_text

    # Avoid creating duplicate calendar events for the same visit
    lead = conversations.get_lead(phone)
    if lead.get("visit_scheduled"):
        logger.info("Visit already scheduled for %s, skipping duplicate event.", phone)
        return clean_text

    property_title = visit_data.get("property", "Propiedad")
    date_str = visit_data.get("date", "")
    time_str = visit_data.get("time", "")

    if not date_str or not time_str:
        logger.warning("Incomplete visit data, skipping calendar event: %s", visit_data)
        return clean_text

    # Get client name and address
    client_name = lead.get("name", "") or ""
    address = _find_address(property_title)

    success = calendar_client.create_visit_event(
        property_title=property_title,
        date_str=date_str,
        time_str=time_str,
        client_phone=phone,
        client_name=client_name,
        address=address,
    )

    if success:
        conversations.update_lead(phone, visit_scheduled=True)
        logger.info("Visit scheduled for %s (%s): %s %s %s", phone, client_name, property_title, date_str, time_str)
        _notify_visit(property_title, address, client_name, date_str, time_str)
        _schedule_reminder(property_title, address, client_name, date_str, time_str)
        if address:
            clean_text = clean_text.rstrip() + f"\n\nDirección: {address}"
    else:
        logger.error("Could not create calendar event for %s", phone)

    return clean_text
