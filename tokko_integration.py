"""
Tokko Broker CRM integration.
Pushes qualified leads and visit data to Tokko via their API.

Configuration:
  TOKKO_API_KEY — agency API key (from tokkobroker.com → Mi Empresa → Permisos)
  TOKKO_ENABLED — "true" to enable (default: false)
"""
import json
import logging
import os
import threading

import requests

logger = logging.getLogger(__name__)

TOKKO_API_URL = "https://tokkobroker.com/portals/simple_portal/api/v1/contact/"
TOKKO_API_KEY = os.environ.get("TOKKO_API_KEY", "")
TOKKO_ENABLED = os.environ.get("TOKKO_ENABLED", "false").lower() in ("true", "1", "yes")

_TIMEOUT = 15


def is_enabled() -> bool:
    return TOKKO_ENABLED and bool(TOKKO_API_KEY)


def push_lead(name: str, phone: str, email: str = "",
              operation: str = "", property_type: str = "",
              budget: str = "", timeline: str = "",
              comment: str = "", publication_id: str = "") -> bool:
    """Push a qualified lead to Tokko as a new contact.

    Args:
        name: lead name
        phone: phone number
        email: email if available
        operation: comprar/alquilar
        property_type: departamento/casa/etc
        budget: budget range
        timeline: when they want to move
        comment: free-text (defaults to auto-generated summary)
        publication_id: Tokko property ID if lead asked about a specific one

    Returns True if successful.
    """
    if not is_enabled():
        return False

    if not comment:
        parts = []
        if operation:
            parts.append(f"Operación: {operation}")
        if property_type:
            parts.append(f"Tipo: {property_type}")
        if budget:
            parts.append(f"Presupuesto: {budget}")
        if timeline:
            parts.append(f"Plazo: {timeline}")
        parts.append("Lead calificado automáticamente por PropBot")
        comment = ". ".join(parts)

    payload = {
        "api_key": TOKKO_API_KEY,
        "name": name or "Sin nombre",
        "cellphone": phone,
        "mail": email or "",
        "comment": comment,
    }

    if publication_id:
        payload["publication_id"] = publication_id

    def _send():
        try:
            resp = requests.post(
                TOKKO_API_URL,
                json=payload,
                timeout=_TIMEOUT,
            )
            if resp.ok:
                result = resp.json() if resp.text else {}
                logger.info("Tokko lead pushed OK: %s → %s", name, result)
            else:
                logger.warning("Tokko push failed (%d): %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.error("Tokko push error: %s", e)

    threading.Thread(target=_send, daemon=True).start()
    return True


def push_visit(name: str, phone: str, property_title: str,
               date: str, time: str, comment: str = "",
               publication_id: str = "") -> bool:
    """Push a visit as a contact with visit details in the comment.

    Tokko's API doesn't have a native visit/activity endpoint,
    so we create a contact with the visit info in the comment field.
    """
    if not is_enabled():
        return False

    visit_comment = (
        f"VISITA AGENDADA por PropBot\n"
        f"Propiedad: {property_title}\n"
        f"Fecha: {date} a las {time}\n"
    )
    if comment:
        visit_comment += f"\n{comment}"

    return push_lead(
        name=name,
        phone=phone,
        comment=visit_comment,
        publication_id=publication_id,
    )
