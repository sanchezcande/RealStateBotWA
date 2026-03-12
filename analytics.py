"""
Analytics event store.
Persists bot events to SQLite for the dashboard.
"""
import hashlib
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_DB_PATH = os.environ.get("ANALYTICS_DB_PATH", "analytics.db")
_conn: Optional[sqlite3.Connection] = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(_DB_PATH, check_same_thread=False, isolation_level=None)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
    return _conn


def init_db():
    """Create tables if they don't exist. Call once at app startup."""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type    TEXT NOT NULL,
            phone_hash    TEXT NOT NULL,
            channel       TEXT DEFAULT 'whatsapp',
            property      TEXT,
            operation     TEXT,
            property_type TEXT,
            hour          INTEGER,
            created_at    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_type    ON events(event_type);
        CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
        CREATE INDEX IF NOT EXISTS idx_events_hour    ON events(hour);

        CREATE TABLE IF NOT EXISTS conversations (
            phone_hash     TEXT PRIMARY KEY,
            channel        TEXT DEFAULT 'whatsapp',
            first_seen_at  TEXT NOT NULL,
            last_seen_at   TEXT NOT NULL,
            message_count  INTEGER DEFAULT 0,
            became_lead    INTEGER DEFAULT 0,
            visit_count    INTEGER DEFAULT 0,
            operation      TEXT,
            property_type  TEXT
        );
    """)
    logger.info("Analytics DB initialised at %s", _DB_PATH)


def _hash_phone(phone: str) -> str:
    return hashlib.sha256(phone.encode()).hexdigest()[:16]


def log_event(event_type: str, phone: str, channel: str = "whatsapp", **kwargs):
    """
    Log an analytics event.
    kwargs: property, operation, property_type
    """
    try:
        conn = _get_conn()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        phone_hash = _hash_phone(phone)
        hour = datetime.now(timezone.utc).hour

        conn.execute(
            """INSERT INTO events (event_type, phone_hash, channel, property, operation,
                                   property_type, hour, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_type,
                phone_hash,
                channel,
                kwargs.get("property"),
                kwargs.get("operation"),
                kwargs.get("property_type"),
                hour,
                now,
            ),
        )

        # Upsert conversation row
        existing = conn.execute(
            "SELECT phone_hash, message_count, became_lead, visit_count FROM conversations WHERE phone_hash = ?",
            (phone_hash,),
        ).fetchone()

        if existing is None:
            conn.execute(
                """INSERT INTO conversations
                   (phone_hash, channel, first_seen_at, last_seen_at, message_count,
                    became_lead, visit_count, operation, property_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    phone_hash, channel, now, now, 1 if event_type == "message_in" else 0,
                    1 if event_type == "lead_qualified" else 0,
                    1 if event_type == "visit_scheduled" else 0,
                    kwargs.get("operation"), kwargs.get("property_type"),
                ),
            )
        else:
            updates = {"last_seen_at": now}
            if event_type == "message_in":
                updates["message_count"] = existing[1] + 1
            if event_type == "lead_qualified":
                updates["became_lead"] = 1
                if kwargs.get("operation"):
                    updates["operation"] = kwargs["operation"]
            if event_type == "visit_scheduled":
                updates["visit_count"] = existing[3] + 1
            if event_type == "new_conversation" and kwargs.get("operation"):
                updates["operation"] = kwargs["operation"]
            if event_type == "new_conversation" and kwargs.get("property_type"):
                updates["property_type"] = kwargs["property_type"]

            set_clause = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(
                f"UPDATE conversations SET {set_clause} WHERE phone_hash = ?",
                (*updates.values(), phone_hash),
            )

    except Exception as e:
        logger.error("analytics.log_event error (%s): %s", event_type, e)


def get_dashboard_data(days: int = 30) -> dict:
    """Return all aggregated metrics for the dashboard as a JSON-serializable dict."""
    try:
        conn = _get_conn()

        # --- KPIs ---
        total_convs = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        total_leads = conn.execute("SELECT COUNT(*) FROM conversations WHERE became_lead = 1").fetchone()[0]
        total_visits = conn.execute("SELECT SUM(visit_count) FROM conversations").fetchone()[0] or 0
        conv_to_lead = round(total_leads / total_convs * 100, 1) if total_convs else 0
        conv_to_visit = round(total_visits / total_convs * 100, 1) if total_convs else 0

        # --- Conversations per day (last N days) ---
        rows = conn.execute(
            """SELECT DATE(created_at) as day, COUNT(*) as cnt
               FROM events
               WHERE event_type = 'new_conversation'
                 AND created_at >= DATE('now', ?)
               GROUP BY day
               ORDER BY day""",
            (f"-{days} days",),
        ).fetchall()
        conv_by_day = {"labels": [r[0] for r in rows], "values": [r[1] for r in rows]}

        # --- Peak hours (filtered to same date range) ---
        rows = conn.execute(
            """SELECT hour, COUNT(*) as cnt
               FROM events
               WHERE event_type = 'message_in'
                 AND created_at >= DATE('now', ?)
               GROUP BY hour
               ORDER BY hour""",
            (f"-{days} days",),
        ).fetchall()
        # Fill all 24 hours
        hour_map = {r[0]: r[1] for r in rows}
        peak_hours = {
            "labels": [f"{h:02d}:00" for h in range(24)],
            "values": [hour_map.get(h, 0) for h in range(24)],
        }

        # --- Top properties by visits scheduled ---
        rows = conn.execute(
            """SELECT property, COUNT(*) as cnt
               FROM events
               WHERE event_type = 'visit_scheduled'
                 AND property IS NOT NULL
               GROUP BY property
               ORDER BY cnt DESC
               LIMIT 10"""
        ).fetchall()
        top_properties = {"labels": [r[0] for r in rows], "values": [r[1] for r in rows]}

        # --- Operation split ---
        rows = conn.execute(
            """SELECT operation, COUNT(*) as cnt
               FROM conversations
               WHERE operation IS NOT NULL
               GROUP BY operation"""
        ).fetchall()
        op_split = {"labels": [r[0] for r in rows], "values": [r[1] for r in rows]}

        # --- Channel split ---
        rows = conn.execute(
            """SELECT channel, COUNT(*) as cnt
               FROM conversations
               GROUP BY channel"""
        ).fetchall()
        channel_split = {"labels": [r[0] for r in rows], "values": [r[1] for r in rows]}

        # --- Escalated to human vs resolved by bot (Pro+) ---
        escalated = conn.execute(
            """SELECT COUNT(DISTINCT phone_hash) FROM events
               WHERE event_type = 'callback_requested'"""
        ).fetchone()[0]
        bot_resolved = max(total_convs - escalated, 0)
        escalation_split = {
            "labels": ["Resueltas por bot", "Escaladas a humano"],
            "values": [bot_resolved, escalated],
        }

        # --- Lead quality split (Premium) ---
        leads_qualified = conn.execute(
            "SELECT COUNT(*) FROM conversations WHERE became_lead = 1"
        ).fetchone()[0]
        no_interaction = conn.execute(
            "SELECT COUNT(*) FROM conversations WHERE became_lead = 0 AND message_count <= 2"
        ).fetchone()[0]
        cold = conn.execute(
            "SELECT COUNT(*) FROM conversations WHERE became_lead = 0 AND message_count > 2"
        ).fetchone()[0]
        lead_quality_split = {
            "labels": ["Leads calificados", "Interaccion sin calificar", "Sin interaccion"],
            "values": [leads_qualified, cold, no_interaction],
        }

        # --- Period comparison: current vs previous same period ---
        prev_convs = conn.execute(
            """SELECT COUNT(*) FROM events
               WHERE event_type = 'new_conversation'
                 AND created_at >= DATE('now', ?)
                 AND created_at < DATE('now', ?)""",
            (f"-{days * 2} days", f"-{days} days"),
        ).fetchone()[0]
        prev_visits = conn.execute(
            """SELECT COUNT(*) FROM events
               WHERE event_type = 'visit_scheduled'
                 AND created_at >= DATE('now', ?)
                 AND created_at < DATE('now', ?)""",
            (f"-{days * 2} days", f"-{days} days"),
        ).fetchone()[0]
        period_comparison = {
            "current_convs": total_convs,
            "prev_convs": prev_convs,
            "current_visits": int(total_visits),
            "prev_visits": prev_visits,
        }

        return {
            "kpis": {
                "total_conversations": total_convs,
                "total_leads": total_leads,
                "total_visits": int(total_visits),
                "conv_to_lead_pct": conv_to_lead,
                "conv_to_visit_pct": conv_to_visit,
            },
            "conv_by_day": conv_by_day,
            "peak_hours": peak_hours,
            "top_properties": top_properties,
            "op_split": op_split,
            "channel_split": channel_split,
            "escalation_split": escalation_split,
            "lead_quality_split": lead_quality_split,
            "period_comparison": period_comparison,
        }
    except Exception as e:
        logger.error("analytics.get_dashboard_data error: %s", e)
        return {}
