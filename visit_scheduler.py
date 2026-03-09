"""
Visit scheduling logic.
Detects hidden <!--visit:{...}--> tags in AI responses,
creates Google Calendar events, and sends confirmation to the client.
"""
import re
import json
import logging

import calendar_client
import conversations
import sheets

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


def _find_address(property_title: str) -> str:
    """Look up the address of a property by title from the current listings."""
    listings = sheets.get_listings()
    title_lower = property_title.lower()
    for p in listings:
        if p.get("titulo", "").lower() == title_lower:
            addr = str(p.get("direccion", "") or "").strip()
            return addr if addr and addr != "Consultar" else ""
    return ""


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

    # Get client name and address
    lead = conversations.get_lead(phone)
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
        logger.info("Visit scheduled for %s (%s): %s %s %s", phone, client_name, property_title, date_str, time_str)
        if address:
            clean_text = clean_text.rstrip() + f"\n\nDirección: {address}"
    else:
        logger.error("Could not create calendar event for %s", phone)

    return clean_text
