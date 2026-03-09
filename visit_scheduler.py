"""
Visit scheduling logic.
Detects hidden <!--visit:{...}--> tags in AI responses,
creates Google Calendar events, and sends confirmation to the client.
"""
import re
import json
import logging

import calendar_client
import whatsapp

logger = logging.getLogger(__name__)

VISIT_TAG_RE = re.compile(r"<!--visit:(.*?)-->", re.DOTALL)


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


def process(phone: str, ai_text: str) -> str:
    """
    Check AI response for a visit tag. If found, create calendar event.
    Returns cleaned text (tag removed).
    """
    visit_data = extract_visit_data(ai_text)
    clean_text = clean_response(ai_text)

    if not visit_data:
        return clean_text

    property_title = visit_data.get("property", "Propiedad")
    date_str = visit_data.get("date", "")
    time_str = visit_data.get("time", "")

    if not date_str or not time_str:
        logger.warning("Incomplete visit data, skipping calendar event: %s", visit_data)
        return clean_text

    success = calendar_client.create_visit_event(property_title, date_str, time_str, phone)

    if success:
        logger.info("Visit scheduled for %s: %s %s %s", phone, property_title, date_str, time_str)
    else:
        logger.error("Could not create calendar event for %s", phone)

    return clean_text
