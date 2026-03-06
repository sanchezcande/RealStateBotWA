"""
Lead qualification logic.
Parses AI responses to detect when budget, operation type, and timeline are captured.
When fully qualified, fires a WhatsApp notification to the agent.
"""
import re
import logging
from config import NOTIFY_NUMBER
import conversations
import whatsapp

logger = logging.getLogger(__name__)

# Keywords the AI must include in its reply to signal qualification data was captured.
# The AI is instructed (via system prompt) to append a JSON block when it detects lead info.
# Format: <!--lead:{"budget":"...","operation":"...","timeline":"...","name":"..."}-->

LEAD_TAG_RE = re.compile(r"<!--lead:(.*?)-->", re.DOTALL)


def extract_lead_data(ai_text: str):
    """Extract embedded lead JSON from AI response, if present."""
    import json
    match = LEAD_TAG_RE.search(ai_text)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        logger.warning("Could not parse lead JSON: %s", match.group(1))
        return None


def clean_response(ai_text: str) -> str:
    """Remove the hidden lead tag from the text before sending to user."""
    return LEAD_TAG_RE.sub("", ai_text).strip()


def is_qualified(lead: dict) -> bool:
    return bool(lead.get("budget") and lead.get("operation") and lead.get("timeline"))


def process(phone: str, ai_text: str) -> str:
    """
    Check AI response for lead data, update state, notify agent if newly qualified.
    Returns cleaned text to send to user.
    """
    lead_data = extract_lead_data(ai_text)
    clean_text = clean_response(ai_text)

    if lead_data:
        update = {k: v for k, v in lead_data.items() if v}
        conversations.update_lead(phone, **update)
        logger.info("Lead updated for %s: %s", phone, update)

    current_lead = conversations.get_lead(phone)

    if is_qualified(current_lead) and not current_lead.get("notified"):
        _notify_agent(phone, current_lead)
        conversations.update_lead(phone, notified=True)

    return clean_text


def _notify_agent(phone: str, lead: dict):
    name = lead.get("name") or "Sin nombre"
    operation = lead.get("operation", "")
    budget = lead.get("budget", "")
    timeline = lead.get("timeline", "")

    msg = (
        f"*NUEVO LEAD CALIFICADO*\n"
        f"Telefono: +{phone}\n"
        f"Nombre: {name}\n"
        f"Operacion: {operation}\n"
        f"Presupuesto: {budget}\n"
        f"Plazo: {timeline}\n\n"
        f"Responde cuanto antes para no perder la oportunidad."
    )
    success = whatsapp.send_message(NOTIFY_NUMBER, msg)
    if success:
        logger.info("Agent notified for lead %s", phone)
    else:
        logger.error("Failed to notify agent for lead %s", phone)
