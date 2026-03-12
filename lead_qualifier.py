"""
Lead qualification logic.
Parses AI responses to detect lead data and callback requests.
Notifies the agent via WhatsApp when a lead is qualified or a callback is requested.
"""
import re
import json
import logging
from config import NOTIFY_NUMBER
import conversations
import whatsapp

logger = logging.getLogger(__name__)

LEAD_TAG_RE = re.compile(r"<!--lead:(.*?)-->", re.DOTALL)
CALLBACK_TAG_RE = re.compile(r"<!--callback:(.*?)-->", re.DOTALL)


def extract_lead_data(ai_text: str):
    match = LEAD_TAG_RE.search(ai_text)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        logger.warning("Could not parse lead JSON: %s", match.group(1))
        return None


def extract_callback_data(ai_text: str):
    match = CALLBACK_TAG_RE.search(ai_text)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        logger.warning("Could not parse callback JSON: %s", match.group(1))
        return None


def clean_response(ai_text: str) -> str:
    """Remove all hidden tags from the text before sending to user."""
    text = LEAD_TAG_RE.sub("", ai_text)
    text = CALLBACK_TAG_RE.sub("", text)
    return text.strip()


def is_qualified(lead: dict) -> bool:
    return bool(lead.get("budget") and lead.get("operation") and lead.get("timeline"))


def process(phone: str, ai_text: str) -> str:
    """
    Check AI response for lead and callback tags, update state, notify agent as needed.
    Returns cleaned text to send to user.
    """
    lead_data = extract_lead_data(ai_text)
    callback_data = extract_callback_data(ai_text)
    clean_text = clean_response(ai_text)

    # Update lead state
    if lead_data:
        update = {k: v for k, v in lead_data.items() if v}
        conversations.update_lead(phone, **update)
        logger.info("Lead updated for %s: %s", phone, update)

    current_lead = conversations.get_lead(phone)

    # Notify agent if lead is newly qualified
    if is_qualified(current_lead) and not current_lead.get("notified"):
        _notify_lead(phone, current_lead)
        conversations.update_lead(phone, notified=True)

    # Notify agent if client requested a callback
    if callback_data:
        _notify_callback(phone, callback_data, current_lead)

    return clean_text


def _notify_lead(phone: str, lead: dict):
    name = lead.get("name") or "Sin nombre"
    summary = conversations.get_conversation_summary(phone)
    msg = (
        f"*NUEVO LEAD CALIFICADO*\n"
        f"Telefono: +{phone}\n"
        f"Nombre: {name}\n"
        f"Operacion: {lead.get('operation', '')}\n"
        f"Presupuesto: {lead.get('budget', '')}\n"
        f"Plazo: {lead.get('timeline', '')}\n\n"
        f"Resumen de la charla:\n{summary}\n\n"
        f"Respondé cuanto antes para no perder la oportunidad."
    )
    success = whatsapp.send_message(NOTIFY_NUMBER, msg)
    if success:
        logger.info("Agent notified for lead %s", phone)
    else:
        logger.error("Failed to notify agent for lead %s", phone)


def _notify_callback(phone: str, callback_data: dict, lead: dict):
    name = lead.get("name") or "Sin nombre"
    preferred_time = callback_data.get("preferred_time") or "No especificado"
    callback_phone = callback_data.get("phone") or f"+{phone}"

    # Last 3 user messages only
    all_messages = conversations.get_messages(phone)
    user_msgs = [m["content"] for m in all_messages if m["role"] == "user"]
    last_user = user_msgs[-3:] if len(user_msgs) >= 3 else user_msgs
    summary = "\n".join(f"- {m[:150]}" for m in last_user)

    msg = (
        f"Cliente quiere que lo llamen.\n"
        f"Nombre: {name}\n"
        f"Teléfono: {callback_phone}\n"
        f"Horario preferido: {preferred_time}\n"
        f"Resumen: {summary}"
    )
    success = whatsapp.send_message(NOTIFY_NUMBER, msg)
    if success:
        logger.info("Callback notification sent for %s", phone)
    else:
        logger.error("Failed to send callback notification for %s", phone)
