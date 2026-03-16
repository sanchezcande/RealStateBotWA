"""
Analytics event store.
Persists bot events to SQLite for the dashboard.
"""
from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Optional

import pytz

logger = logging.getLogger(__name__)

AR_TZ = pytz.timezone("America/Argentina/Buenos_Aires")
_DB_PATH = os.environ.get("ANALYTICS_DB_PATH", "analytics.db")
_conn: Optional[sqlite3.Connection] = None
_db_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(_DB_PATH, check_same_thread=False, isolation_level=None)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
    return _conn


def init_db():
    """Create tables if they don't exist. Call once at app startup."""
    with _db_lock:
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

            CREATE TABLE IF NOT EXISTS chat_messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                phone       TEXT NOT NULL,
                phone_hash  TEXT NOT NULL,
                role        TEXT NOT NULL,
                content     TEXT NOT NULL,
                channel     TEXT DEFAULT 'whatsapp',
                created_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_chat_phone   ON chat_messages(phone);
            CREATE INDEX IF NOT EXISTS idx_chat_hash    ON chat_messages(phone_hash);
            CREATE INDEX IF NOT EXISTS idx_chat_created ON chat_messages(created_at);

            CREATE TABLE IF NOT EXISTS leads (
                phone       TEXT PRIMARY KEY,
                phone_hash  TEXT NOT NULL,
                name        TEXT,
                operation   TEXT,
                property_type TEXT,
                budget      TEXT,
                timeline    TEXT,
                notified    INTEGER DEFAULT 0,
                channel     TEXT DEFAULT 'whatsapp',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_leads_hash    ON leads(phone_hash);
            CREATE INDEX IF NOT EXISTS idx_leads_updated ON leads(updated_at);

            CREATE TABLE IF NOT EXISTS visits (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                phone           TEXT NOT NULL,
                phone_hash      TEXT NOT NULL,
                client_name     TEXT,
                property_title  TEXT NOT NULL,
                address         TEXT,
                visit_date      TEXT NOT NULL,
                visit_time      TEXT NOT NULL,
                calendar_event_id TEXT,
                status          TEXT DEFAULT 'confirmed',
                channel         TEXT DEFAULT 'whatsapp',
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_visits_date   ON visits(visit_date);
            CREATE INDEX IF NOT EXISTS idx_visits_phone  ON visits(phone);
            CREATE INDEX IF NOT EXISTS idx_visits_status ON visits(status);

            CREATE TABLE IF NOT EXISTS media_usage (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                month           TEXT NOT NULL,
                videos_used     INTEGER DEFAULT 0,
                videos_purchased INTEGER DEFAULT 0,
                updated_at      TEXT NOT NULL,
                UNIQUE(month)
            );
        """)
        # Seed mock data so the dashboard has something to show.
        # Runs if DB is empty OR if SEED_DEMO_DATA=true (force reseed).
        # Skipped for in-memory DBs (tests).
        if _DB_PATH != ":memory:":
            force_seed = os.environ.get("SEED_DEMO_DATA", "").lower() in ("true", "1", "yes")
            count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            if count == 0 or force_seed:
                if force_seed and count > 0:
                    for tbl in ("events", "conversations", "chat_messages", "leads", "visits"):
                        conn.execute(f"DELETE FROM {tbl}")
                    logger.info("Cleared existing data for demo reseed")
                _seed_mock_data(conn)
    logger.info("Analytics DB initialised at %s", _DB_PATH)


def _seed_mock_data(conn):
    """Insert realistic demo data so the dashboard isn't empty on first load."""
    import random

    logger.info("Seeding mock data for dashboard demo...")

    properties = [
        "Depto 3 amb Palermo", "Casa 4 amb Belgrano", "Monoambiente Recoleta",
        "PH 2 amb Villa Crespo", "Depto 2 amb Caballito", "Casa 3 amb Nuñez",
        "Loft San Telmo", "Depto 4 amb Puerto Madero", "Casa 5 amb Olivos",
        "Depto 2 amb Almagro",
    ]
    names = [
        "Martin", "Lucia", "Santiago", "Valentina", "Mateo",
        "Camila", "Nicolas", "Sofia", "Tomas", "Julieta",
        "Agustin", "Florencia", "Lautaro", "Martina", "Facundo",
        "Carolina", "Gonzalo", "Milagros", "Federico", "Rocio",
        "Pablo", "Maria", "Ezequiel", "Daniela", "Ignacio",
    ]
    operations = ["comprar", "alquilar"]
    prop_types = ["departamento", "casa", "monoambiente", "PH"]
    channels = ["whatsapp", "whatsapp", "whatsapp", "facebook", "instagram"]  # Mostly WA
    budgets = ["USD 80.000-120.000", "USD 150.000-200.000", "$300.000/mes",
               "$450.000/mes", "USD 250.000+", "$200.000-350.000/mes"]
    timelines = ["1-2 meses", "3-6 meses", "urgente", "explorando", "este mes"]

    greetings = [
        "Hola, estoy buscando {op} un {tipo}",
        "Buenas, me interesa {op} un {tipo} en zona norte",
        "Hola soy {name}, quiero {op}",
        "Buenas tardes, busco {tipo} para {op}",
        "Hola, vi una publicacion de ustedes y me interesa",
    ]
    responses_user = [
        "Si, me interesa mucho", "Podemos coordinar una visita?",
        "Cual es el precio?", "Tiene cochera?", "Me podes mandar fotos?",
        "Genial, cuando podemos ir?", "Tiene balcon?", "Es luminoso?",
        "Cuantos metros tiene?", "Acepta mascotas?",
        "Me gusta, quiero verlo", "Gracias por la info",
        "Puede ser el sabado?", "Prefiero por la tarde",
        "Tienen algo mas grande?", "Y las expensas?",
    ]
    responses_bot = [
        "Dale, te cuento sobre las opciones que tenemos",
        "Tenemos varias propiedades que te pueden interesar",
        "Perfecto, te paso los detalles",
        "La propiedad tiene 65m2 con balcon al frente",
        "Las expensas son de $45.000 aproximadamente",
        "Podemos coordinar una visita para esta semana",
        "Te confirmo la visita para el jueves a las 15hs",
        "Tiene 2 dormitorios, living comedor y cocina integrada",
        "Si, acepta mascotas sin problema",
        "Te mando las fotos por aca",
    ]

    now = datetime.now(AR_TZ)
    phone_counter = 5491100000000

    for i in range(25):
        phone = str(phone_counter + i)
        phone_hash = _hash_phone(phone)
        ch = random.choice(channels)
        name = names[i % len(names)]
        op = random.choice(operations)
        pt = random.choice(prop_types)
        is_lead = random.random() < 0.6
        has_visit = is_lead and random.random() < 0.5
        msg_count = random.randint(3, 18)

        # Spread conversations over last 30 days
        days_ago = random.randint(0, 29)
        base_time = now - __import__("datetime").timedelta(days=days_ago)
        first_seen = base_time.strftime("%Y-%m-%dT%H:%M:%S")
        last_offset = random.randint(0, min(days_ago, 5))
        last_time = (now - __import__("datetime").timedelta(days=last_offset))
        last_seen = last_time.strftime("%Y-%m-%dT%H:%M:%S")

        visit_count = 1 if has_visit else 0

        # Insert conversation analytics
        conn.execute(
            """INSERT INTO conversations
               (phone_hash, channel, first_seen_at, last_seen_at,
                message_count, became_lead, visit_count, operation, property_type)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (phone_hash, ch, first_seen, last_seen,
             msg_count, 1 if is_lead else 0, visit_count, op, pt),
        )

        # Insert events
        hour = random.choice([9, 10, 11, 14, 15, 16, 17, 18, 19, 20])
        event_time = base_time.replace(hour=hour).strftime("%Y-%m-%dT%H:%M:%S")

        conn.execute(
            "INSERT INTO events (event_type, phone_hash, channel, hour, created_at) VALUES (?,?,?,?,?)",
            ("new_conversation", phone_hash, ch, hour, event_time),
        )
        for m in range(msg_count):
            msg_offset = random.randint(0, days_ago * 24 * 60)
            msg_time = (base_time + __import__("datetime").timedelta(minutes=msg_offset)).strftime("%Y-%m-%dT%H:%M:%S")
            msg_hour = random.choice(range(8, 22))
            conn.execute(
                "INSERT INTO events (event_type, phone_hash, channel, hour, created_at) VALUES (?,?,?,?,?)",
                ("message_in", phone_hash, ch, msg_hour, msg_time),
            )

        if is_lead:
            conn.execute(
                "INSERT INTO events (event_type, phone_hash, channel, operation, property_type, created_at) VALUES (?,?,?,?,?,?)",
                ("lead_qualified", phone_hash, ch, op, pt, last_seen),
            )

        if has_visit:
            prop = random.choice(properties)
            conn.execute(
                "INSERT INTO events (event_type, phone_hash, channel, property, operation, created_at) VALUES (?,?,?,?,?,?)",
                ("visit_scheduled", phone_hash, ch, prop, op, last_seen),
            )

        # Insert chat messages
        greeting = random.choice(greetings).format(op=op, tipo=pt, name=name)
        conn.execute(
            "INSERT INTO chat_messages (phone, phone_hash, role, content, channel, created_at) VALUES (?,?,?,?,?,?)",
            (phone, phone_hash, "user", f"Hola, soy {name}. " + greeting, ch, event_time),
        )
        conn.execute(
            "INSERT INTO chat_messages (phone, phone_hash, role, content, channel, created_at) VALUES (?,?,?,?,?,?)",
            (phone, phone_hash, "assistant", random.choice(responses_bot), ch, event_time),
        )
        for m in range(min(msg_count - 1, 8)):
            msg_offset = (m + 1) * random.randint(10, 120)
            msg_dt = (base_time + __import__("datetime").timedelta(minutes=msg_offset))
            conn.execute(
                "INSERT INTO chat_messages (phone, phone_hash, role, content, channel, created_at) VALUES (?,?,?,?,?,?)",
                (phone, phone_hash, "user", random.choice(responses_user), ch, msg_dt.strftime("%Y-%m-%dT%H:%M:%S")),
            )
            conn.execute(
                "INSERT INTO chat_messages (phone, phone_hash, role, content, channel, created_at) VALUES (?,?,?,?,?,?)",
                (phone, phone_hash, "assistant", random.choice(responses_bot), ch, msg_dt.strftime("%Y-%m-%dT%H:%M:%S")),
            )

        # Insert lead record
        if is_lead:
            budget = random.choice(budgets)
            timeline = random.choice(timelines)
            conn.execute(
                """INSERT INTO leads (phone, phone_hash, name, operation, property_type,
                   budget, timeline, notified, channel, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (phone, phone_hash, name, op, pt, budget, timeline,
                 1, ch, first_seen, last_seen),
            )

        # Insert visit record
        if has_visit:
            prop = random.choice(properties)
            visit_days_ahead = random.randint(-5, 14)
            visit_date = (now + __import__("datetime").timedelta(days=visit_days_ahead)).strftime("%Y-%m-%d")
            visit_time = f"{random.choice([10,11,14,15,16,17])}:{random.choice(['00','30'])}"
            status = "confirmed" if visit_days_ahead >= 0 else random.choice(["confirmed", "cancelled"])
            conn.execute(
                """INSERT INTO visits (phone, phone_hash, client_name, property_title,
                   address, visit_date, visit_time, status, channel, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (phone, phone_hash, name, prop,
                 f"Av. Ejemplo {random.randint(100,9999)}", visit_date, visit_time,
                 status, ch, first_seen, last_seen),
            )

    # Add a few callback_requested events for escalation metrics
    for i in range(4):
        phone = str(phone_counter + random.randint(0, 24))
        phone_hash = _hash_phone(phone)
        days_ago = random.randint(0, 20)
        event_time = (now - __import__("datetime").timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute(
            "INSERT INTO events (event_type, phone_hash, channel, created_at) VALUES (?,?,?,?)",
            ("callback_requested", phone_hash, "whatsapp", event_time),
        )

    logger.info("Mock data seeded: 25 conversations, leads, visits, and messages")


def _hash_phone(phone: str) -> str:
    return hashlib.sha256(phone.encode()).hexdigest()[:16]


def health_check() -> bool:
    """Return True if the analytics database is accessible."""
    try:
        with _db_lock:
            _get_conn().execute("SELECT 1").fetchone()
        return True
    except Exception:
        return False


def log_event(event_type: str, phone: str, channel: str = "whatsapp", **kwargs):
    """
    Log an analytics event.
    kwargs: property, operation, property_type
    """
    try:
        with _db_lock:
            conn = _get_conn()
            now = datetime.now(AR_TZ).strftime("%Y-%m-%dT%H:%M:%S")
            phone_hash = _hash_phone(phone)
            hour = datetime.now(AR_TZ).hour

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
                if event_type == "visit_cancelled":
                    updates["visit_count"] = max(existing[3] - 1, 0)
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
        with _db_lock:
            conn = _get_conn()
            now = datetime.now(AR_TZ)
            cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
            prev_cutoff = (now - timedelta(days=days * 2)).strftime("%Y-%m-%dT%H:%M:%S")

            # --- KPIs (filtered by date range) ---
            total_convs = conn.execute(
                "SELECT COUNT(*) FROM conversations WHERE last_seen_at >= ?",
                (cutoff,),
            ).fetchone()[0]
            total_leads = conn.execute(
                "SELECT COUNT(*) FROM conversations WHERE became_lead = 1 AND last_seen_at >= ?",
                (cutoff,),
            ).fetchone()[0]
            total_visits = conn.execute(
                "SELECT COALESCE(SUM(visit_count), 0) FROM conversations WHERE last_seen_at >= ?",
                (cutoff,),
            ).fetchone()[0]
            conv_to_lead = round(total_leads / total_convs * 100, 1) if total_convs else 0
            conv_to_visit = round(total_visits / total_convs * 100, 1) if total_convs else 0

            # --- Conversations per day (last N days) ---
            rows = conn.execute(
                """SELECT DATE(created_at) as day, COUNT(*) as cnt
                   FROM events
                   WHERE event_type = 'new_conversation'
                     AND created_at >= ?
                   GROUP BY day
                   ORDER BY day""",
                (cutoff,),
            ).fetchall()
            conv_by_day = {"labels": [r[0] for r in rows], "values": [r[1] for r in rows]}

            # --- Peak hours (filtered) ---
            rows = conn.execute(
                """SELECT hour, COUNT(*) as cnt
                   FROM events
                   WHERE event_type = 'message_in'
                     AND created_at >= ?
                   GROUP BY hour
                   ORDER BY hour""",
                (cutoff,),
            ).fetchall()
            hour_map = {r[0]: r[1] for r in rows}
            peak_hours = {
                "labels": [f"{h:02d}:00" for h in range(24)],
                "values": [hour_map.get(h, 0) for h in range(24)],
            }

            # --- Top properties by requested visits (filtered) ---
            rows = conn.execute(
                """SELECT property_title, COALESCE(MAX(NULLIF(address, '')), ''), COUNT(*) as cnt
                   FROM visits
                   WHERE created_at >= ?
                   GROUP BY property_title
                   ORDER BY cnt DESC, property_title ASC
                   LIMIT 10""",
                (cutoff,),
            ).fetchall()
            top_property_items = [
                {
                    "title": r[0],
                    "address": r[1],
                    "count": r[2],
                }
                for r in rows
            ]
            top_properties = {
                "labels": [item["title"] for item in top_property_items],
                "values": [item["count"] for item in top_property_items],
                "items": top_property_items,
            }

            # --- Operation split (filtered) ---
            rows = conn.execute(
                """SELECT operation, COUNT(*) as cnt
                   FROM conversations
                   WHERE operation IS NOT NULL
                     AND last_seen_at >= ?
                   GROUP BY operation""",
                (cutoff,),
            ).fetchall()
            _op_display = {"comprar": "Venta", "alquilar": "Alquiler"}
            op_split = {
                "labels": [_op_display.get(r[0], r[0]) for r in rows],
                "values": [r[1] for r in rows],
            }

            # --- Channel split (filtered) ---
            rows = conn.execute(
                """SELECT channel, COUNT(*) as cnt
                   FROM conversations
                   WHERE last_seen_at >= ?
                   GROUP BY channel""",
                (cutoff,),
            ).fetchall()
            channel_aliases = {
                "whatsapp": ("whatsapp", "WhatsApp"),
                "instagram": ("instagram", "Instagram"),
                "facebook": ("facebook", "Facebook"),
                "page": ("facebook", "Facebook"),
                "messenger": ("facebook", "Facebook"),
                "meta": ("social_legacy", "Instagram/Facebook"),
            }
            channel_counts = {}
            for raw_channel, count in rows:
                normalized = (raw_channel or "whatsapp").strip().lower()
                key, label = channel_aliases.get(
                    normalized,
                    (normalized or "other", normalized.title() or "Otro"),
                )
                bucket = channel_counts.setdefault(key, {"label": label, "count": 0})
                bucket["count"] += count

            preferred_order = ["whatsapp", "instagram", "facebook", "social_legacy"]
            ordered_channels = [
                {"key": key, **channel_counts[key]}
                for key in preferred_order
                if key in channel_counts
            ]
            for key, item in channel_counts.items():
                if key not in preferred_order:
                    ordered_channels.append({"key": key, **item})

            channel_breakdown = []
            for item in ordered_channels:
                count = item["count"]
                pct = round(count / total_convs * 100, 1) if total_convs else 0
                channel_breakdown.append({
                    "key": item["key"],
                    "label": item["label"],
                    "count": count,
                    "pct": pct,
                })

            channel_split = {
                "labels": [item["label"] for item in channel_breakdown],
                "values": [item["count"] for item in channel_breakdown],
                "items": channel_breakdown,
            }

            # --- Escalated to human vs resolved by bot (filtered) ---
            escalated = conn.execute(
                """SELECT COUNT(DISTINCT phone_hash) FROM events
                   WHERE event_type = 'callback_requested'
                     AND created_at >= ?""",
                (cutoff,),
            ).fetchone()[0]
            bot_resolved = max(total_convs - escalated, 0)
            escalation_split = {
                "labels": ["Resueltas por bot", "Escaladas a humano"],
                "values": [bot_resolved, escalated],
            }

            # --- Lead quality split (filtered) ---
            leads_qualified = conn.execute(
                "SELECT COUNT(*) FROM conversations WHERE became_lead = 1 AND last_seen_at >= ?",
                (cutoff,),
            ).fetchone()[0]
            no_interaction = conn.execute(
                "SELECT COUNT(*) FROM conversations WHERE became_lead = 0 AND message_count <= 2 AND last_seen_at >= ?",
                (cutoff,),
            ).fetchone()[0]
            cold = conn.execute(
                "SELECT COUNT(*) FROM conversations WHERE became_lead = 0 AND message_count > 2 AND last_seen_at >= ?",
                (cutoff,),
            ).fetchone()[0]
            lead_quality_split = {
                "labels": ["Leads calificados", "Interaccion sin calificar", "Sin interaccion"],
                "values": [leads_qualified, cold, no_interaction],
            }

            # --- Period comparison: current vs previous same period ---
            prev_convs = conn.execute(
                """SELECT COUNT(*) FROM events
                   WHERE event_type = 'new_conversation'
                     AND created_at >= ?
                     AND created_at < ?""",
                (prev_cutoff, cutoff),
            ).fetchone()[0]
            prev_visits = conn.execute(
                """SELECT COUNT(*) FROM events
                   WHERE event_type = 'visit_scheduled'
                     AND created_at >= ?
                     AND created_at < ?""",
                (prev_cutoff, cutoff),
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
            "channel_breakdown": channel_breakdown,
            "escalation_split": escalation_split,
            "lead_quality_split": lead_quality_split,
            "period_comparison": period_comparison,
        }
    except Exception as e:
        logger.error("analytics.get_dashboard_data error: %s", e)
        return {}


# ---------------------------------------------------------------------------
# Persistence helpers (used by conversations.py write-through)
# ---------------------------------------------------------------------------

def save_message(phone: str, role: str, content: str, channel: str = "whatsapp"):
    """Persist a single chat message to SQLite."""
    try:
        with _db_lock:
            conn = _get_conn()
            now = datetime.now(AR_TZ).strftime("%Y-%m-%dT%H:%M:%S")
            conn.execute(
                "INSERT INTO chat_messages (phone, phone_hash, role, content, channel, created_at) VALUES (?,?,?,?,?,?)",
                (phone, _hash_phone(phone), role, content, channel, now),
            )
    except Exception as e:
        logger.error("analytics.save_message error: %s", e)


def load_messages(phone: str) -> list:
    """Load all messages for a phone from SQLite. Returns list of {role, content}."""
    try:
        with _db_lock:
            conn = _get_conn()
            rows = conn.execute(
                "SELECT role, content FROM chat_messages WHERE phone = ? ORDER BY id",
                (phone,),
            ).fetchall()
        return [{"role": r[0], "content": r[1]} for r in rows]
    except Exception as e:
        logger.error("analytics.load_messages error: %s", e)
        return []


def upsert_lead(phone: str, channel: str = "whatsapp", **fields):
    """Insert or update a lead record in SQLite."""
    try:
        with _db_lock:
            conn = _get_conn()
            now = datetime.now(AR_TZ).strftime("%Y-%m-%dT%H:%M:%S")
            phone_hash = _hash_phone(phone)
            existing = conn.execute("SELECT phone FROM leads WHERE phone = ?", (phone,)).fetchone()
            if existing is None:
                conn.execute(
                    """INSERT INTO leads (phone, phone_hash, name, operation, property_type,
                       budget, timeline, notified, channel, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        phone, phone_hash,
                        fields.get("name"), fields.get("operation"),
                        fields.get("property_type"), fields.get("budget"),
                        fields.get("timeline"),
                        1 if fields.get("notified") else 0,
                        channel, now, now,
                    ),
                )
            else:
                parts, vals = [], []
                for col in ("name", "operation", "property_type", "budget", "timeline"):
                    if fields.get(col) is not None:
                        parts.append(f"{col} = ?")
                        vals.append(fields[col])
                if "notified" in fields:
                    parts.append("notified = ?")
                    vals.append(1 if fields["notified"] else 0)
                if parts:
                    parts.append("updated_at = ?")
                    vals.append(now)
                    vals.append(phone)
                    conn.execute(f"UPDATE leads SET {', '.join(parts)} WHERE phone = ?", vals)
    except Exception as e:
        logger.error("analytics.upsert_lead error: %s", e)


def load_lead(phone: str) -> dict | None:
    """Load a lead record from SQLite. Returns dict or None."""
    try:
        with _db_lock:
            conn = _get_conn()
            row = conn.execute(
                "SELECT name, operation, property_type, budget, timeline, notified FROM leads WHERE phone = ?",
                (phone,),
            ).fetchone()
        if row is None:
            return None
        return {
            "name": row[0], "operation": row[1], "property_type": row[2],
            "budget": row[3], "timeline": row[4], "notified": bool(row[5]),
        }
    except Exception as e:
        logger.error("analytics.load_lead error: %s", e)
        return None


def save_visit(phone: str, property_title: str, address: str, client_name: str,
               date_str: str, time_str: str, event_id: str | None = None,
               channel: str = "whatsapp"):
    """Insert a visit record."""
    try:
        with _db_lock:
            conn = _get_conn()
            now = datetime.now(AR_TZ).strftime("%Y-%m-%dT%H:%M:%S")
            conn.execute(
                """INSERT INTO visits (phone, phone_hash, client_name, property_title,
                   address, visit_date, visit_time, calendar_event_id, status, channel,
                   created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (phone, _hash_phone(phone), client_name or None, property_title,
                 address or None, date_str, time_str, event_id, "confirmed",
                 channel, now, now),
            )
    except Exception as e:
        logger.error("analytics.save_visit error: %s", e)


def cancel_visit(phone: str, property_title: str, date_str: str, time_str: str):
    """Mark a visit as cancelled."""
    try:
        with _db_lock:
            conn = _get_conn()
            now = datetime.now(AR_TZ).strftime("%Y-%m-%dT%H:%M:%S")
            conn.execute(
                """UPDATE visits SET status = 'cancelled', updated_at = ?
                   WHERE phone = ? AND property_title = ? AND visit_date = ? AND visit_time = ?
                   AND status = 'confirmed'""",
                (now, phone, property_title, date_str, time_str),
            )
    except Exception as e:
        logger.error("analytics.cancel_visit error: %s", e)


# ---------------------------------------------------------------------------
# Dashboard API queries
# ---------------------------------------------------------------------------

def get_conversations_list(page: int = 1, per_page: int = 20, search: str = "",
                           channel: str = "", status: str = "") -> dict:
    """Return paginated conversation list for the dashboard."""
    try:
        with _db_lock:
            conn = _get_conn()
            where, params = ["1=1"], []

            if search:
                where.append("(l.name LIKE ? OR cm.phone LIKE ? OR cm.content LIKE ?)")
                s = f"%{search}%"
                params.extend([s, s, s])
            if channel:
                where.append("cm.channel = ?")
                params.append(channel)

            # Get distinct phones with latest message info
            base_query = f"""
                SELECT cm.phone, cm.phone_hash, cm.channel,
                       MIN(cm.created_at) as first_msg,
                       MAX(cm.created_at) as last_msg,
                       COUNT(cm.id) as msg_count,
                       l.name,
                       COALESCE(c.became_lead, 0) as is_lead,
                       COALESCE(c.visit_count, 0) as visits
                FROM chat_messages cm
                LEFT JOIN leads l ON cm.phone = l.phone
                LEFT JOIN conversations c ON cm.phone_hash = c.phone_hash
                WHERE {' AND '.join(where)}
                GROUP BY cm.phone
            """

            if status == "lead":
                base_query += " HAVING is_lead = 1"
            elif status == "visit":
                base_query += " HAVING visits > 0"

            # Count total
            count_q = f"SELECT COUNT(*) FROM ({base_query})"
            total = conn.execute(count_q, params).fetchone()[0]

            # Get page
            query = f"{base_query} ORDER BY last_msg DESC LIMIT ? OFFSET ?"
            params.extend([per_page, (page - 1) * per_page])
            rows = conn.execute(query, params).fetchall()

            # Get last message preview for each
            items = []
            for r in rows:
                phone = r[0]
                last_row = conn.execute(
                    "SELECT content, role FROM chat_messages WHERE phone = ? ORDER BY id DESC LIMIT 1",
                    (phone,),
                ).fetchone()
                items.append({
                    "phone": phone[:4] + "****" + phone[-4:] if len(phone) > 8 else phone,
                    "phone_hash": r[1],
                    "channel": r[2],
                    "first_message": r[3],
                    "last_message": r[4],
                    "message_count": r[5],
                    "name": r[6] or "",
                    "is_lead": bool(r[7]),
                    "visit_count": r[8],
                    "last_preview": (last_row[0][:80] + "...") if last_row and len(last_row[0]) > 80 else (last_row[0] if last_row else ""),
                    "last_role": last_row[1] if last_row else "",
                })

        return {"items": items, "total": total, "page": page, "per_page": per_page}
    except Exception as e:
        logger.error("analytics.get_conversations_list error: %s", e)
        return {"items": [], "total": 0, "page": page, "per_page": per_page}


def get_conversation_thread(phone_hash: str) -> dict:
    """Return full message thread for a conversation by phone_hash."""
    try:
        with _db_lock:
            conn = _get_conn()
            rows = conn.execute(
                """SELECT role, content, created_at, channel
                   FROM chat_messages WHERE phone_hash = ?
                   ORDER BY id""",
                (phone_hash,),
            ).fetchall()
            lead_row = conn.execute(
                "SELECT name, operation, property_type, budget, timeline, notified FROM leads WHERE phone_hash = ?",
                (phone_hash,),
            ).fetchone()
        messages = [{"role": r[0], "content": r[1], "time": r[2], "channel": r[3]} for r in rows]
        lead = None
        if lead_row:
            lead = {
                "name": lead_row[0], "operation": lead_row[1],
                "property_type": lead_row[2], "budget": lead_row[3],
                "timeline": lead_row[4], "notified": bool(lead_row[5]),
            }
        return {"messages": messages, "lead": lead}
    except Exception as e:
        logger.error("analytics.get_conversation_thread error: %s", e)
        return {"messages": [], "lead": None}


def resolve_phone_by_hash(phone_hash: str) -> str | None:
    """Resolve a phone_hash back to the real phone number."""
    try:
        with _db_lock:
            conn = _get_conn()
            row = conn.execute(
                "SELECT DISTINCT phone FROM chat_messages WHERE phone_hash = ? LIMIT 1",
                (phone_hash,),
            ).fetchone()
        return row[0] if row else None
    except Exception as e:
        logger.error("analytics.resolve_phone_by_hash error: %s", e)
        return None


def get_leads_list(page: int = 1, per_page: int = 20, operation: str = "",
                   sort: str = "updated_at") -> dict:
    """Return paginated leads list."""
    try:
        with _db_lock:
            conn = _get_conn()
            where, params = ["1=1"], []
            if operation:
                where.append("l.operation = ?")
                params.append(operation)

            allowed_sorts = {"updated_at", "created_at", "name"}
            sort_col = sort if sort in allowed_sorts else "updated_at"

            count = conn.execute(
                f"SELECT COUNT(*) FROM leads l WHERE {' AND '.join(where)}", params
            ).fetchone()[0]

            query = f"""
                SELECT l.phone, l.phone_hash, l.name, l.operation, l.property_type,
                       l.budget, l.timeline, l.notified, l.channel, l.created_at, l.updated_at,
                       COALESCE(c.message_count, 0), COALESCE(c.visit_count, 0)
                FROM leads l
                LEFT JOIN conversations c ON l.phone_hash = c.phone_hash
                WHERE {' AND '.join(where)}
                ORDER BY l.{sort_col} DESC
                LIMIT ? OFFSET ?
            """
            params.extend([per_page, (page - 1) * per_page])
            rows = conn.execute(query, params).fetchall()

        items = [{
            "phone": r[0][:4] + "****" + r[0][-4:] if len(r[0]) > 8 else r[0],
            "phone_hash": r[1], "name": r[2] or "", "operation": r[3] or "",
            "property_type": r[4] or "", "budget": r[5] or "", "timeline": r[6] or "",
            "notified": bool(r[7]), "channel": r[8], "created_at": r[9],
            "updated_at": r[10], "message_count": r[11], "visit_count": r[12],
        } for r in rows]
        return {"items": items, "total": count, "page": page, "per_page": per_page}
    except Exception as e:
        logger.error("analytics.get_leads_list error: %s", e)
        return {"items": [], "total": 0, "page": page, "per_page": per_page}


def get_visits_list(date_from: str = "", date_to: str = "", status: str = "",
                    page: int = 1, per_page: int = 50) -> dict:
    """Return paginated visits list."""
    try:
        with _db_lock:
            conn = _get_conn()
            where, params = ["1=1"], []
            if date_from:
                where.append("v.visit_date >= ?")
                params.append(date_from)
            if date_to:
                where.append("v.visit_date <= ?")
                params.append(date_to)
            if status:
                where.append("v.status = ?")
                params.append(status)

            count = conn.execute(
                f"SELECT COUNT(*) FROM visits v WHERE {' AND '.join(where)}", params
            ).fetchone()[0]

            query = f"""
                SELECT v.id, v.phone, v.phone_hash, v.client_name, v.property_title,
                       v.address, v.visit_date, v.visit_time, v.calendar_event_id,
                       v.status, v.channel, v.created_at
                FROM visits v
                WHERE {' AND '.join(where)}
                ORDER BY v.visit_date DESC, v.visit_time DESC
                LIMIT ? OFFSET ?
            """
            params.extend([per_page, (page - 1) * per_page])
            rows = conn.execute(query, params).fetchall()

        items = [{
            "id": r[0],
            "phone": r[1][:4] + "****" + r[1][-4:] if len(r[1]) > 8 else r[1],
            "phone_hash": r[2], "client_name": r[3] or "", "property_title": r[4],
            "address": r[5] or "", "visit_date": r[6], "visit_time": r[7],
            "has_calendar": bool(r[8]), "status": r[9], "channel": r[10],
            "created_at": r[11],
        } for r in rows]
        return {"items": items, "total": count, "page": page, "per_page": per_page}
    except Exception as e:
        logger.error("analytics.get_visits_list error: %s", e)
        return {"items": [], "total": 0, "page": page, "per_page": per_page}


def get_visits_calendar(month: str) -> dict:
    """Return visits grouped by date for a given month (YYYY-MM)."""
    try:
        with _db_lock:
            conn = _get_conn()
            rows = conn.execute(
                """SELECT visit_date, visit_time, property_title, client_name, status
                   FROM visits
                   WHERE visit_date LIKE ?
                   ORDER BY visit_date, visit_time""",
                (f"{month}%",),
            ).fetchall()
        by_date = {}
        for r in rows:
            day = r[0]
            if day not in by_date:
                by_date[day] = []
            by_date[day].append({
                "time": r[1], "property": r[2],
                "client": r[3] or "", "status": r[4],
            })
        return {"month": month, "days": by_date}
    except Exception as e:
        logger.error("analytics.get_visits_calendar error: %s", e)
        return {"month": month, "days": {}}


# ---------------------------------------------------------------------------
# Media usage tracking (video generation limits)
# ---------------------------------------------------------------------------

FREE_VIDEOS_PER_MONTH = int(os.environ.get("FREE_VIDEOS_PER_MONTH", "4"))
EXTRA_VIDEO_PRICE_USD = 25


def get_media_usage(month: str = "") -> dict:
    """Return video usage for the given month (YYYY-MM). Defaults to current month."""
    if not month:
        month = datetime.now(AR_TZ).strftime("%Y-%m")
    try:
        with _db_lock:
            conn = _get_conn()
            row = conn.execute(
                "SELECT videos_used, videos_purchased FROM media_usage WHERE month = ?",
                (month,),
            ).fetchone()
        used = row[0] if row else 0
        purchased = row[1] if row else 0
        total_allowed = FREE_VIDEOS_PER_MONTH + purchased
        return {
            "month": month,
            "videos_used": used,
            "videos_purchased": purchased,
            "free_limit": FREE_VIDEOS_PER_MONTH,
            "total_allowed": total_allowed,
            "remaining": max(total_allowed - used, 0),
            "extra_video_price_usd": EXTRA_VIDEO_PRICE_USD,
        }
    except Exception as e:
        logger.error("analytics.get_media_usage error: %s", e)
        return {
            "month": month, "videos_used": 0, "videos_purchased": 0,
            "free_limit": FREE_VIDEOS_PER_MONTH, "total_allowed": FREE_VIDEOS_PER_MONTH,
            "remaining": FREE_VIDEOS_PER_MONTH, "extra_video_price_usd": EXTRA_VIDEO_PRICE_USD,
        }


def increment_video_usage() -> bool:
    """Increment video usage for the current month. Returns True if within limit."""
    month = datetime.now(AR_TZ).strftime("%Y-%m")
    try:
        with _db_lock:
            conn = _get_conn()
            now = datetime.now(AR_TZ).strftime("%Y-%m-%dT%H:%M:%S")
            row = conn.execute(
                "SELECT videos_used, videos_purchased FROM media_usage WHERE month = ?",
                (month,),
            ).fetchone()

            used = row[0] if row else 0
            purchased = row[1] if row else 0
            total_allowed = FREE_VIDEOS_PER_MONTH + purchased

            if used >= total_allowed:
                return False

            if row is None:
                conn.execute(
                    "INSERT INTO media_usage (month, videos_used, videos_purchased, updated_at) VALUES (?,?,?,?)",
                    (month, 1, 0, now),
                )
            else:
                conn.execute(
                    "UPDATE media_usage SET videos_used = videos_used + 1, updated_at = ? WHERE month = ?",
                    (now, month),
                )
            return True
    except Exception as e:
        logger.error("analytics.increment_video_usage error: %s", e)
        return False


def add_purchased_videos(count: int = 1) -> dict:
    """Add purchased videos to the current month's allowance."""
    month = datetime.now(AR_TZ).strftime("%Y-%m")
    try:
        with _db_lock:
            conn = _get_conn()
            now = datetime.now(AR_TZ).strftime("%Y-%m-%dT%H:%M:%S")
            row = conn.execute(
                "SELECT videos_purchased FROM media_usage WHERE month = ?",
                (month,),
            ).fetchone()

            if row is None:
                conn.execute(
                    "INSERT INTO media_usage (month, videos_used, videos_purchased, updated_at) VALUES (?,?,?,?)",
                    (month, 0, count, now),
                )
            else:
                conn.execute(
                    "UPDATE media_usage SET videos_purchased = videos_purchased + ?, updated_at = ? WHERE month = ?",
                    (count, now, month),
                )
        return get_media_usage(month)
    except Exception as e:
        logger.error("analytics.add_purchased_videos error: %s", e)
        return get_media_usage(month)
