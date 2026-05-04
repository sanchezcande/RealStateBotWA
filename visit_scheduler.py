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
import crm_webhook
import sheets
import tokko_integration
import whatsapp
from config import NOTIFY_NUMBER, AR_TZ

logger = logging.getLogger(__name__)

VISIT_TAG_RE = re.compile(r"<!--visit:(.*?)-->", re.DOTALL)
CANCEL_TAG_RE = re.compile(r"<!--cancel_visit:(.*?)-->", re.DOTALL)
NOTIFY_VISIT_TAG_RE = re.compile(r"<!--notify_visit:(.*?)-->", re.DOTALL)

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
    """Remove all hidden visit/cancel/notify tags from the text before sending to user."""
    text = VISIT_TAG_RE.sub("", ai_text)
    text = CANCEL_TAG_RE.sub("", text)
    text = NOTIFY_VISIT_TAG_RE.sub("", text)
    return text.strip()


def _find_listing(property_title: str) -> dict | None:
    """Look up a listing by title. Returns the listing dict or None."""
    listings = sheets.get_listings()
    title_lower = property_title.lower()
    for p in listings:
        if p.get("titulo", "").lower() == title_lower:
            return p
    return None


def _find_address(property_title: str) -> str:
    """Look up the address of a property by title from the current listings."""
    p = _find_listing(property_title)
    if p:
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


def _notify_visit_failed(property_title: str, address: str, client_name: str, date_str: str, time_str: str, phone: str = ""):
    """Notify agent that a calendar event could not be created, so they can handle it manually."""
    address_str = address or "Sin dirección cargada"
    name_str = client_name or "Sin nombre"
    msg = (
        f"ERROR: No se pudo crear el evento en el calendario.\n"
        f"Propiedad: {property_title}\n"
        f"Dirección: {address_str}\n"
        f"Cliente: {name_str}\n"
        f"Horario solicitado: {date_str} a las {time_str}\n"
        f"Tel: {phone}\n\n"
        f"La visita NO quedó registrada. Por favor agendala manualmente."
    )
    success = whatsapp.send_message(NOTIFY_NUMBER, msg)
    if success:
        logger.info("Agent notified of calendar failure for: %s on %s %s", property_title, date_str, time_str)
    else:
        logger.error("Failed to notify agent of calendar failure for %s", property_title)


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


def _extract_notify_visit_data(ai_text: str) -> list:
    """Extract <!--notify_visit:{...}--> tags (notify mode)."""
    results = []
    for match in NOTIFY_VISIT_TAG_RE.finditer(ai_text):
        try:
            results.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            logger.warning("Could not parse notify_visit JSON: %s", match.group(1))
    return results


def _notify_visit_request(property_title: str, client_name: str, phone: str):
    """Notify agent that a client wants to visit a property (notify mode)."""
    name_str = client_name or "Sin nombre"
    summary = conversations.get_conversation_summary(phone) if phone else "(sin mensajes)"
    msg = (
        f"Solicitud de visita!\n"
        f"Propiedad: {property_title}\n"
        f"Cliente: {name_str}\n"
        f"Tel: {phone}\n\n"
        f"Resumen de la charla:\n{summary}"
    )
    success = whatsapp.send_message(NOTIFY_NUMBER, msg)
    if success:
        logger.info("Agent notified of visit request: %s for %s", property_title, name_str)
    else:
        logger.error("Failed to notify agent of visit request for %s", property_title)


def process(phone: str, ai_text: str) -> str:
    """
    Check AI response for visit/cancel/notify tags. Handles multiple visits in a single message.
    Creates/deletes calendar events, notifies agent, schedules reminders.
    Returns cleaned text (tags removed).
    """
    all_visit_data = extract_all_visit_data(ai_text)
    cancel_data = extract_cancel_data(ai_text)
    notify_data = _extract_notify_visit_data(ai_text)
    clean_text = clean_response(ai_text)

    # Handle notify mode visit requests
    if notify_data:
        lead = conversations.get_lead(phone)
        client_name = lead.get("name", "") or ""
        for nd in notify_data:
            property_title = nd.get("property", "Propiedad")
            _notify_visit_request(property_title, client_name, phone)
            analytics.log_event("visit_request_notified", phone, property=property_title)

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
        if not event_id:
            existing = analytics.get_visit_by_key(phone, property_title, date_str, time_str)
            if existing:
                event_id = existing.get("calendar_event_id")
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

        existing = analytics.get_visit_by_key(phone, property_title, date_str, time_str)
        if existing and existing.get("status") == "confirmed":
            if existing.get("calendar_event_id"):
                logger.info("Visit already exists in DB for %s, skipping: %s", phone, visit_key)
                continue
            logger.info("Visit exists without calendar_event_id for %s — attempting to create event", phone)

        listing = _find_listing(property_title)
        address = ""
        property_id = None
        if listing:
            addr = str(listing.get("direccion", "") or "").strip()
            address = addr if addr and addr != "Consultar" else ""
            property_id = str(listing.get("id", "") or "").strip() or None
        event_id = calendar_client.create_visit_event(
            property_title=property_title,
            date_str=date_str,
            time_str=time_str,
            client_phone=phone,
            client_name=client_name,
            address=address,
        )

        if event_id is None:
            logger.error("Failed to create calendar event for %s / %s %s %s — visit NOT tracked", phone, property_title, date_str, time_str)
            _notify_visit_failed(property_title, address, client_name, date_str, time_str, phone=phone)
            continue

        scheduled_visits.append(visit_key)
        visit_events[visit_key] = event_id
        conversations.update_lead(
            phone,
            visit_scheduled=True,
            scheduled_visits=scheduled_visits,
            visit_events=visit_events,
        )
        _schedule_reminder(property_title, address, client_name, date_str, time_str)
        logger.info("Visit scheduled for %s (%s): %s %s %s", phone, client_name, property_title, date_str, time_str)
        analytics.log_event("visit_scheduled", phone, property=property_title,
                             operation=lead.get("operation"))
        analytics.save_visit(phone, property_title, address, client_name,
                             date_str, time_str, event_id=event_id,
                             property_id=property_id)
        analytics.update_visit_event_id(phone, property_title, date_str, time_str, event_id)
        crm_webhook.on_visit_scheduled(
            phone_hash=analytics._hash_phone(phone),
            client_name=client_name,
            property_title=property_title,
            date=date_str,
            time=time_str,
        )
        _notify_visit(property_title, address, client_name, date_str, time_str, phone=phone)
        tokko_integration.push_visit(
            name=client_name,
            phone=phone,
            property_title=property_title,
            date=date_str,
            time=time_str,
        )

    # Do NOT append address to user-visible text after confirming visits.
    # The prompt explicitly forbids sharing the exact address at confirmation time.

    return clean_text
