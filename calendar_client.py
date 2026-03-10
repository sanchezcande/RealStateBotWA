"""
Google Calendar integration.
Uses the same service account credentials as Google Sheets.
The calendar must be shared with the service account email.
"""
import json
import logging
from datetime import datetime, timedelta, date
import pytz

from config import GOOGLE_CREDENTIALS_JSON, GOOGLE_CALENDAR_ID

logger = logging.getLogger(__name__)

AR_TZ = pytz.timezone("America/Argentina/Buenos_Aires")


def _get_service():
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    scopes = ["https://www.googleapis.com/auth/calendar"]
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def get_free_slots(days_ahead: int = 5, hour_start: int = 9, hour_end: int = 18) -> list:
    """
    Return a list of free 1-hour slots over the next `days_ahead` working days.
    Each slot is a dict: {"date": "YYYY-MM-DD", "time": "HH:MM", "label": "lunes 10/3 a las 10:00"}
    Returns empty list if calendar is not configured or on error.
    """
    if not GOOGLE_CALENDAR_ID:
        return []

    try:
        service = _get_service()

        now = datetime.now(AR_TZ)
        # Start from next hour to avoid suggesting times already past
        start_window = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        end_window = start_window + timedelta(days=days_ahead + 1)

        # Fetch busy intervals from Google Calendar
        body = {
            "timeMin": start_window.isoformat(),
            "timeMax": end_window.isoformat(),
            "timeZone": "America/Argentina/Buenos_Aires",
            "items": [{"id": GOOGLE_CALENDAR_ID}],
        }
        result = service.freebusy().query(body=body).execute()
        busy_intervals = result["calendars"][GOOGLE_CALENDAR_ID]["busy"]

        busy_ranges = []
        for interval in busy_intervals:
            b_start = datetime.fromisoformat(interval["start"]).astimezone(AR_TZ)
            b_end = datetime.fromisoformat(interval["end"]).astimezone(AR_TZ)
            busy_ranges.append((b_start, b_end))

        DAYS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
        MONTHS_ES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

        free_slots = []
        check_date = start_window.date()

        while len(free_slots) < 6 and check_date <= end_window.date():
            weekday = check_date.weekday()
            if weekday < 6:  # Mon–Sat
                for hour in range(hour_start, hour_end):
                    slot_start = AR_TZ.localize(datetime(check_date.year, check_date.month, check_date.day, hour))
                    slot_end = slot_start + timedelta(hours=1)

                    # Skip slots in the past
                    if slot_start <= now:
                        continue

                    # Check if slot overlaps with any busy interval
                    is_busy = any(b_start < slot_end and b_end > slot_start for b_start, b_end in busy_ranges)
                    if not is_busy:
                        day_name = DAYS_ES[weekday]
                        month_name = MONTHS_ES[check_date.month - 1]
                        label = f"{day_name} {check_date.day}/{check_date.month} a las {hour:02d}:00"
                        free_slots.append({
                            "date": check_date.strftime("%Y-%m-%d"),
                            "time": f"{hour:02d}:00",
                            "label": label,
                        })
                    if len(free_slots) >= 6:
                        break
            check_date += timedelta(days=1)

        return free_slots

    except Exception as e:
        logger.error("Failed to fetch free slots: %s", e)
        return []


def create_visit_event(
    property_title: str,
    date_str: str,
    time_str: str,
    client_phone: str,
    client_name: str = "",
    address: str = "",
) -> bool:
    """
    Create a property visit event on the calendar.
    date_str: "YYYY-MM-DD"
    time_str: "HH:MM"
    Returns True on success.
    """
    if not GOOGLE_CALENDAR_ID:
        logger.warning("GOOGLE_CALENDAR_ID not set — skipping calendar event creation.")
        return False

    try:
        service = _get_service()

        start_dt = AR_TZ.localize(datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M"))
        end_dt = start_dt + timedelta(hours=1)

        name_str = client_name if client_name else "Sin nombre"
        address_str = address if address else "Sin dirección cargada"
        description = (
            f"Cliente: {name_str}\n"
            f"Teléfono: +{client_phone}\n"
            f"Propiedad: {property_title}\n"
            f"Dirección: {address_str}\n"
            f"Coordinado por WhatsApp"
        )

        title_parts = [address_str if address_str != "Sin dirección cargada" else property_title]
        if client_name:
            title_parts.append(client_name)
        event = {
            "summary": " — ".join(title_parts),
            "description": description,
            "location": address_str,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "America/Argentina/Buenos_Aires"},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": "America/Argentina/Buenos_Aires"},
        }

        service.events().insert(calendarId=GOOGLE_CALENDAR_ID, body=event).execute()
        logger.info("Calendar event created: %s on %s %s for %s", property_title, date_str, time_str, client_phone)
        return True

    except Exception as e:
        logger.error("Failed to create calendar event: %s", e)
        return False
