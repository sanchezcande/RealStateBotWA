"""
Conversation store with write-through SQLite persistence.
Keeps an in-memory cache for fast AI pipeline access while persisting
every message and lead update to the analytics database.
"""
import time as _time
from collections import defaultdict
from threading import Lock

import analytics

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
    },
    "_loaded": False,  # whether we've loaded from DB for this phone
    "_last_access": 0,
})
_lock = Lock()

MAX_HISTORY = 40  # messages kept per conversation (~20 exchanges)
_MAX_CACHED = 500  # max conversations kept in memory
_CACHE_TTL = 24 * 60 * 60  # evict after 24h of inactivity


def _ensure_loaded(phone: str):
    """Lazy-load from DB on first access after restart. Must be called inside _lock."""
    entry = _store[phone]
    entry["_last_access"] = _time.time()
    if entry["_loaded"]:
        return
    entry["_loaded"] = True
    # Load messages from SQLite
    db_msgs = analytics.load_messages(phone)
    if db_msgs and not entry["messages"]:
        entry["messages"] = db_msgs[-MAX_HISTORY:]
    # Load lead from SQLite
    db_lead = analytics.load_lead(phone)
    if db_lead:
        for k, v in db_lead.items():
            if v is not None:
                entry["lead"][k] = v
    # Evict stale entries if cache is too large
    if len(_store) > _MAX_CACHED:
        _evict_stale()


def _evict_stale():
    """Remove oldest inactive conversations from cache. Must be called inside _lock."""
    now = _time.time()
    stale = [p for p, e in _store.items() if now - e["_last_access"] > _CACHE_TTL]
    for p in stale:
        del _store[p]


def get(phone: str) -> dict:
    with _lock:
        _ensure_loaded(phone)
        return _store[phone]


def add_message(phone: str, role: str, content: str, channel: str = "whatsapp"):
    with _lock:
        _ensure_loaded(phone)
        msgs = _store[phone]["messages"]
        msgs.append({"role": role, "content": content})
        if len(msgs) > MAX_HISTORY:
            _store[phone]["messages"] = msgs[-MAX_HISTORY:]
    # Write-through to SQLite (outside lock to avoid holding it during IO)
    analytics.save_message(phone, role, content, channel=channel)


def update_lead(phone: str, **kwargs):
    with _lock:
        _ensure_loaded(phone)
        _store[phone]["lead"].update(kwargs)
    # Persist serializable lead fields to SQLite
    db_fields = {}
    for col in ("name", "operation", "property_type", "budget", "timeline", "notified"):
        if col in kwargs and kwargs[col] is not None:
            db_fields[col] = kwargs[col]
    if db_fields:
        analytics.upsert_lead(phone, **db_fields)


def get_lead(phone: str) -> dict:
    with _lock:
        _ensure_loaded(phone)
        return dict(_store[phone]["lead"])


def get_messages(phone: str) -> list:
    with _lock:
        _ensure_loaded(phone)
        return list(_store[phone]["messages"])


_agent_takeover: dict = {}  # phone -> {"until": timestamp}
_TAKEOVER_TTL = 30 * 60  # 30 minutes


def set_agent_takeover(phone: str, duration: int = _TAKEOVER_TTL):
    """Pause AI auto-replies for this conversation."""
    with _lock:
        _agent_takeover[phone] = {"until": _time.time() + duration}


def clear_agent_takeover(phone: str):
    """Resume AI auto-replies for this conversation."""
    with _lock:
        _agent_takeover.pop(phone, None)


def is_agent_takeover(phone: str) -> bool:
    """Check if AI is paused for this conversation."""
    with _lock:
        info = _agent_takeover.get(phone)
        if not info:
            return False
        if _time.time() > info["until"]:
            _agent_takeover.pop(phone, None)
            return False
        return True


def get_conversation_summary(phone: str, n_messages: int = 4) -> str:
    """Return last n user messages as a mini summary string for agent notifications."""
    with _lock:
        _ensure_loaded(phone)
        msgs = _store[phone]["messages"]
        user_msgs = [m["content"] for m in msgs if m["role"] == "user"]
        last_msgs = user_msgs[-n_messages:] if len(user_msgs) >= n_messages else user_msgs
        return "\n".join(f"- {m[:200]}" for m in last_msgs) if last_msgs else "(sin mensajes)"
