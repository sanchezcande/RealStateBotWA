"""
Google Calendar integration.
Uses the same service account credentials as Google Sheets.
The calendar must be shared with the service account email.
"""
import json
import logging
from datetime import datetime, timedelta
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


def create_visit_event(property_title: str, date_str: str, time_str: str, client_phone: str) -> bool:
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

        event = {
            "summary": f"Visita: {property_title}",
            "description": f"Visita coordinada por WhatsApp con el cliente +{client_phone}",
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "America/Argentina/Buenos_Aires"},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": "America/Argentina/Buenos_Aires"},
        }

        service.events().insert(calendarId=GOOGLE_CALENDAR_ID, body=event).execute()
        logger.info("Calendar event created: %s on %s %s", property_title, date_str, time_str)
        return True

    except Exception as e:
        logger.error("Failed to create calendar event: %s", e)
        return False
