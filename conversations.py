"""
In-memory conversation store.
Stores message history and lead qualification state per WhatsApp number.
"""
from collections import defaultdict
from threading import Lock

_store = defaultdict(lambda: {
    "messages": [],      # list of {"role": "user"|"assistant", "content": str}
    "lead": {
        "budget": None,
        "operation": None,      # "comprar" | "alquilar"
        "property_type": None,
        "timeline": None,
        "name": None,
        "notified": False,
        "visit_scheduled": False,
        "scheduled_visits": [],  # list of "property|date|time" keys to avoid duplicates
        "visit_events": {},      # dict mapping "property|date|time" -> Google Calendar event ID
    }
})
_lock = Lock()

MAX_HISTORY = 40  # messages kept per conversation (~20 exchanges)


def get(phone: str) -> dict:
    with _lock:
        return _store[phone]


def add_message(phone: str, role: str, content: str):
    with _lock:
        msgs = _store[phone]["messages"]
        msgs.append({"role": role, "content": content})
        if len(msgs) > MAX_HISTORY:
            _store[phone]["messages"] = msgs[-MAX_HISTORY:]


def update_lead(phone: str, **kwargs):
    with _lock:
        _store[phone]["lead"].update(kwargs)


def get_lead(phone: str) -> dict:
    with _lock:
        return dict(_store[phone]["lead"])


def get_messages(phone: str) -> list:
    with _lock:
        return list(_store[phone]["messages"])


def get_conversation_summary(phone: str, n_messages: int = 4) -> str:
    """Return last n user messages as a mini summary string for agent notifications."""
    with _lock:
        msgs = _store[phone]["messages"]
        user_msgs = [m["content"] for m in msgs if m["role"] == "user"]
        last_msgs = user_msgs[-n_messages:] if len(user_msgs) >= n_messages else user_msgs
        return "\n".join(f"- {m[:200]}" for m in last_msgs) if last_msgs else "(sin mensajes)"
