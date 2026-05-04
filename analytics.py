"""
Analytics event store.
Persists bot events to PostgreSQL (preferred) or SQLite (fallback).
"""
from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import threading
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from config import AR_TZ, ANALYTICS_DB_PATH
import sheets

logger = logging.getLogger(__name__)

# ── Database backend selection ──
# If DATABASE_URL is set (Railway PostgreSQL addon), use PostgreSQL.
# Otherwise fall back to SQLite at ANALYTICS_DB_PATH.
DATABASE_URL = os.environ.get("DATABASE_URL")
_USE_PG = bool(DATABASE_URL)
_DB_PATH = ANALYTICS_DB_PATH  # only used for SQLite fallback

_conn = None
_conn_pid: Optional[int] = None
_db_lock = threading.Lock()
_startup_diag: dict = {}

# Auto-increment primary key syntax differs between backends
_AUTO_PK = "SERIAL PRIMARY KEY" if _USE_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"


def _pg_val(v):
    """Convert PostgreSQL-specific types to JSON-serializable Python types."""
    if isinstance(v, date):
        return v.isoformat() if isinstance(v, datetime) else str(v)
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, memoryview):
        return bytes(v)
    return v


class _PgConn:
    """Wraps a psycopg2 connection to match sqlite3's conn.execute() API.

    This lets us keep ALL existing SQL queries unchanged — the wrapper
    transparently translates ? placeholders to %s for PostgreSQL.
    """

    def __init__(self, dsn: str):
        import psycopg2
        self._conn = psycopg2.connect(dsn)
        self._conn.autocommit = True

    def execute(self, sql: str, params=None):
        sql = sql.replace("?", "%s")
        cur = self._conn.cursor()
        cur.execute(sql, params or ())
        return _PgCursor(cur)

    def close(self):
        self._conn.close()


class _PgCursor:
    """Wraps psycopg2 cursor to convert PG types to SQLite-compatible types."""

    def __init__(self, cur):
        self._cur = cur

    def fetchone(self):
        row = self._cur.fetchone()
        return tuple(_pg_val(v) for v in row) if row else None

    def fetchall(self):
        return [tuple(_pg_val(v) for v in row) for row in self._cur.fetchall()]

    @property
    def rowcount(self):
        return self._cur.rowcount


def _get_conn():
    global _conn, _conn_pid
    current_pid = os.getpid()
    if _conn is not None and _conn_pid != current_pid:
        logger.info("Fork detected (parent=%s, worker=%s) — reopening DB connection", _conn_pid, current_pid)
        _conn = None
    if _conn is None:
        if _USE_PG:
            _conn = _PgConn(DATABASE_URL)
            logger.info("PostgreSQL connection opened (pid=%d)", current_pid)
        else:
            _conn = sqlite3.connect(_DB_PATH, check_same_thread=False, isolation_level=None)
            _conn.execute("PRAGMA journal_mode=DELETE")
            _conn.execute("PRAGMA synchronous=FULL")
            logger.info("SQLite connection opened: %s (pid=%d)", _DB_PATH, current_pid)
        _conn_pid = current_pid
    return _conn


def shutdown_db():
    """Close connection cleanly. Call on app shutdown."""
    global _conn
    with _db_lock:
        if _conn:
            try:
                if not _USE_PG:
                    _conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                _conn.close()
                logger.info("DB shutdown: connection closed")
            except Exception as e:
                logger.error("DB shutdown error: %s", e)
            _conn = None


def db_stats() -> dict:
    """Return row counts for all tables (diagnostic)."""
    try:
        with _db_lock:
            conn = _get_conn()
            tables = ["chat_messages", "leads", "conversations", "events", "visits"]
            counts = {}
            for t in tables:
                try:
                    counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                except Exception:
                    counts[t] = -1
            counts["backend"] = "postgresql" if _USE_PG else "sqlite"
            return counts
    except Exception as e:
        return {"error": str(e)}


def init_db():
    """Create tables if they don't exist. Call once at app startup."""
    global _startup_diag
    diag = {"steps": []}

    def _step(name, data=None):
        diag["steps"].append({"step": name, "data": data})
        logger.info("init_db [%s]: %s", name, data)

    # ── Step 1: Check what exists BEFORE we touch anything ──
    _step("backend", "postgresql" if _USE_PG else "sqlite")
    _step("pid", os.getpid())

    if _USE_PG:
        _step("database_url", DATABASE_URL[:30] + "..." if DATABASE_URL else None)
        # Check if PG has existing data from previous deploys
        try:
            import psycopg2
            tmp = psycopg2.connect(DATABASE_URL)
            tmp.autocommit = True
            cur = tmp.cursor()
            pre = {}
            for tbl in ("chat_messages", "leads", "conversations", "events"):
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {tbl}")
                    pre[tbl] = cur.fetchone()[0]
                except Exception:
                    pre[tbl] = "table_missing"
                    tmp.rollback()
            tmp.close()
            _step("pre_init_rows", pre)
        except Exception as e:
            _step("pre_init_read_error", str(e))
    else:
        _step("db_path", _DB_PATH)
        _step("file_exists", os.path.isfile(_DB_PATH))
        if os.path.isfile(_DB_PATH):
            _step("file_size_bytes", os.path.getsize(_DB_PATH))
            try:
                tmp = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True, timeout=5)
                pre = {}
                for tbl in ("chat_messages", "leads", "conversations", "events"):
                    try:
                        pre[tbl] = tmp.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                    except Exception:
                        pre[tbl] = "table_missing"
                tmp.close()
                _step("pre_init_rows", pre)
            except Exception as e:
                _step("pre_init_read_error", str(e))

    # ── Step 2: Open connection and create tables ──
    with _db_lock:
        conn = _get_conn()

        _ddl = [
            f"""CREATE TABLE IF NOT EXISTS events (
                id {_AUTO_PK}, event_type TEXT NOT NULL,
                phone_hash TEXT NOT NULL, channel TEXT DEFAULT 'whatsapp',
                property TEXT, operation TEXT, property_type TEXT,
                hour INTEGER, created_at TEXT NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)",
            "CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_events_hour ON events(hour)",
            """CREATE TABLE IF NOT EXISTS conversations (
                phone_hash TEXT PRIMARY KEY, channel TEXT DEFAULT 'whatsapp',
                first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
                message_count INTEGER DEFAULT 0, became_lead INTEGER DEFAULT 0,
                visit_count INTEGER DEFAULT 0, operation TEXT, property_type TEXT,
                agent_takeover_until TEXT)""",
            f"""CREATE TABLE IF NOT EXISTS chat_messages (
                id {_AUTO_PK}, phone TEXT NOT NULL,
                phone_hash TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
                channel TEXT DEFAULT 'whatsapp', created_at TEXT NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS idx_chat_phone ON chat_messages(phone)",
            "CREATE INDEX IF NOT EXISTS idx_chat_hash ON chat_messages(phone_hash)",
            "CREATE INDEX IF NOT EXISTS idx_chat_created ON chat_messages(created_at)",
            """CREATE TABLE IF NOT EXISTS leads (
                phone TEXT PRIMARY KEY, phone_hash TEXT NOT NULL, name TEXT,
                operation TEXT, property_type TEXT, budget TEXT, timeline TEXT,
                notified INTEGER DEFAULT 0, channel TEXT DEFAULT 'whatsapp',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS idx_leads_hash ON leads(phone_hash)",
            "CREATE INDEX IF NOT EXISTS idx_leads_updated ON leads(updated_at)",
            f"""CREATE TABLE IF NOT EXISTS visits (
                id {_AUTO_PK}, phone TEXT NOT NULL,
                phone_hash TEXT NOT NULL, client_name TEXT,
                property_title TEXT NOT NULL, address TEXT,
                visit_date TEXT NOT NULL, visit_time TEXT NOT NULL,
                calendar_event_id TEXT, status TEXT DEFAULT 'confirmed',
                channel TEXT DEFAULT 'whatsapp',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS idx_visits_date ON visits(visit_date)",
            "CREATE INDEX IF NOT EXISTS idx_visits_phone ON visits(phone)",
            "CREATE INDEX IF NOT EXISTS idx_visits_status ON visits(status)",
            """CREATE TABLE IF NOT EXISTS media_jobs (
                id TEXT PRIMARY KEY, type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued', progress TEXT DEFAULT '',
                property TEXT DEFAULT '', photo_count INTEGER DEFAULT 0,
                prompt TEXT DEFAULT '', result_path TEXT, result_url TEXT,
                error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS idx_media_jobs_created ON media_jobs(created_at)",
            f"""CREATE TABLE IF NOT EXISTS media_usage (
                id {_AUTO_PK}, month TEXT NOT NULL UNIQUE,
                videos_used INTEGER DEFAULT 0, videos_purchased INTEGER DEFAULT 0,
                updated_at TEXT NOT NULL)""",
            f"""CREATE TABLE IF NOT EXISTS payments (
                id {_AUTO_PK},
                payment_id TEXT UNIQUE NOT NULL,
                provider TEXT NOT NULL DEFAULT 'mercadopago',
                status TEXT NOT NULL DEFAULT 'pending',
                amount REAL NOT NULL DEFAULT 0, currency TEXT NOT NULL DEFAULT 'ARS',
                video_count INTEGER NOT NULL DEFAULT 1, payer_email TEXT DEFAULT '',
                external_ref TEXT DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status)",
            """CREATE TABLE IF NOT EXISTS locks (
                name TEXT PRIMARY KEY,
                acquired_at INTEGER NOT NULL,
                owner TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS processed_messages (
                message_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (message_id, channel))""",
        ]
        for sql in _ddl:
            conn.execute(sql)
        _step("ddl_done", True)

        # Migrations — add columns that may not exist yet
        _migrations = [
            ("conversations", "agent_takeover_until", "TEXT"),
            ("visits", "property_id", "TEXT"),
        ]
        for tbl, col, col_type in _migrations:
            try:
                conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {col_type}")
            except Exception:
                pass  # Column already exists

        # Check rows right after DDL
        post_ddl = {}
        for tbl in ("chat_messages", "leads", "conversations", "events"):
            try:
                post_ddl[tbl] = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            except Exception:
                post_ddl[tbl] = -1
        _step("post_ddl_rows", post_ddl)

        # Purge demo data
        _purge_mock_data(conn)
        post_purge = {}
        for tbl in ("chat_messages", "leads", "conversations", "events"):
            try:
                post_purge[tbl] = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            except Exception:
                post_purge[tbl] = -1
        _step("post_purge_rows", post_purge)

        # Seed mock data ONLY when explicitly requested AND not in production.
        if _DB_PATH != ":memory:":
            is_production = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_SERVICE_ID"))
            force_seed = os.environ.get("SEED_DEMO_DATA", "").lower() in ("true", "1", "yes")
            _step("seed_check", {"SEED_DEMO_DATA": os.environ.get("SEED_DEMO_DATA", ""),
                                 "force_seed": force_seed, "is_production": is_production})
            if force_seed and is_production:
                logger.warning("SEED_DEMO_DATA ignored in production environment!")
            elif force_seed:
                count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                if count > 0:
                    for tbl in ("events", "conversations", "chat_messages", "leads", "visits"):
                        conn.execute(f"DELETE FROM {tbl}")
                    _step("seed_cleared_all", True)
                _seed_mock_data(conn)

    # Cleanup old media jobs
    try:
        cutoff = (datetime.now(AR_TZ) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
        old_files = conn.execute(
            "SELECT result_path FROM media_jobs WHERE created_at < ?", (cutoff,)
        ).fetchall()
        for row in old_files:
            if row[0]:
                try:
                    os.unlink(row[0])
                except OSError:
                    pass
        conn.execute("DELETE FROM media_jobs WHERE created_at < ?", (cutoff,))
    except Exception:
        pass

    # Cleanup old processed message IDs (dedupe table)
    try:
        cutoff = (datetime.now(AR_TZ) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute("DELETE FROM processed_messages WHERE created_at < ?", (cutoff,))
    except Exception:
        pass

    # Final row counts
    final = {}
    for tbl in ("chat_messages", "leads", "conversations", "events"):
        try:
            final[tbl] = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        except Exception:
            final[tbl] = -1
    _step("final_rows", final)
    _step("backend_active", "postgresql" if _USE_PG else "sqlite")

    _startup_diag = diag
    logger.info("init_db complete (%s). Diag: %s", "PG" if _USE_PG else "SQLite", diag)


def _purge_mock_data(conn):
    """Remove all rows associated with demo phone numbers (5491100000000–5491100000024)."""
    demo_hashes = [_hash_phone(str(5491100000000 + i)) for i in range(25)]
    if not demo_hashes:
        return
    placeholders = ",".join("?" * len(demo_hashes))
    tables_with_hash = ["events", "conversations", "leads", "visits"]
    total = 0
    for tbl in tables_with_hash:
        try:
            cur = conn.execute(f"DELETE FROM {tbl} WHERE phone_hash IN ({placeholders})", demo_hashes)
            total += cur.rowcount
        except Exception:
            pass
    # chat_messages uses raw phone, not hash
    demo_phones = [str(5491100000000 + i) for i in range(25)]
    pp = ",".join("?" * len(demo_phones))
    try:
        cur = conn.execute(f"DELETE FROM chat_messages WHERE phone IN ({pp})", demo_phones)
        total += cur.rowcount
    except Exception:
        pass
    if total:
        logger.info("Purged %d demo/mock rows from database", total)


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


def _match_listing(visit_title: str, listings: list) -> dict | None:
    """Find the best matching listing for a visit's property_title.

    Tries: exact titulo match, tipo_propiedad match, then word-overlap.
    """
    vt = visit_title.strip().lower()
    # 1. Exact match on titulo
    for p in listings:
        if (p.get("titulo") or "").strip().lower() == vt:
            return p
    # 2. Exact match on tipo_propiedad
    for p in listings:
        if (p.get("tipo_propiedad") or "").strip().lower() == vt:
            return p
    # 3. Word overlap — pick best match above threshold
    import re
    vt_words = set(re.split(r'[\s/,.\-]+', vt)) - {"", "en", "de", "la", "el", "los", "las", "con", "y", "a"}
    if not vt_words:
        return None
    best, best_score = None, 0.0
    for p in listings:
        for field in ("titulo", "tipo_propiedad"):
            val = (p.get(field) or "").strip().lower()
            if not val:
                continue
            p_words = set(re.split(r'[\s/,.\-]+', val)) - {"", "en", "de", "la", "el", "los", "las", "con", "y", "a"}
            if not p_words:
                continue
            overlap = len(vt_words & p_words)
            score = overlap / max(len(vt_words), len(p_words))
            if score > best_score:
                best_score = score
                best = p
    return best if best_score >= 0.4 else None


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

                _ALLOWED_CONV_COLS = {"last_seen_at", "message_count", "became_lead",
                                     "visit_count", "operation", "property_type"}
                safe_updates = {k: v for k, v in updates.items() if k in _ALLOWED_CONV_COLS}
                if safe_updates:
                    set_clause = ", ".join(f"{k} = ?" for k in safe_updates)
                    conn.execute(
                        f"UPDATE conversations SET {set_clause} WHERE phone_hash = ?",
                        (*safe_updates.values(), phone_hash),
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
            # Count leads from leads table (anyone with at least operation detected)
            # Falls back to conversations.became_lead for backward compat
            total_leads_table = conn.execute(
                "SELECT COUNT(*) FROM leads WHERE (operation IS NOT NULL AND operation != '') AND updated_at >= ?",
                (cutoff,),
            ).fetchone()[0]
            total_leads_conv = conn.execute(
                "SELECT COUNT(*) FROM conversations WHERE became_lead = 1 AND last_seen_at >= ?",
                (cutoff,),
            ).fetchone()[0]
            total_leads = max(total_leads_table, total_leads_conv)
            # Count visits from visits table + visit request events
            total_visits_table = conn.execute(
                "SELECT COUNT(*) FROM visits WHERE created_at >= ? AND status != 'cancelled'",
                (cutoff,),
            ).fetchone()[0]
            total_visits_events = conn.execute(
                "SELECT COUNT(*) FROM events WHERE event_type = 'visit_request_notified' AND created_at >= ?",
                (cutoff,),
            ).fetchone()[0]
            total_visits_conv = conn.execute(
                "SELECT COALESCE(SUM(visit_count), 0) FROM conversations WHERE last_seen_at >= ?",
                (cutoff,),
            ).fetchone()[0]
            total_visits = max(total_visits_table + total_visits_events, int(total_visits_conv))
            conv_to_lead = round(total_leads / total_convs * 100, 1) if total_convs else 0
            conv_to_visit = round(total_visits / total_convs * 100, 1) if total_convs else 0

            # --- Conversations per day (last N days) ---
            rows = conn.execute(
                """SELECT DATE(created_at) as day, COUNT(*) as cnt
                   FROM events
                   WHERE event_type = 'new_conversation'
                     AND created_at >= ?
                   GROUP BY DATE(created_at)
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

            # --- Top properties by visits (confirmed + cancelled breakdown) ---
            rows = conn.execute(
                """SELECT property_title, COALESCE(MAX(NULLIF(address, '')), ''),
                          SUM(CASE WHEN status = 'confirmed' THEN 1 ELSE 0 END) as confirmed,
                          SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) as cancelled,
                          COUNT(*) as total,
                          MAX(property_id) as pid
                   FROM visits
                   WHERE created_at >= ?
                   GROUP BY property_title
                   ORDER BY total DESC, property_title ASC
                   LIMIT 10""",
                (cutoff,),
            ).fetchall()
            top_property_items = [
                {
                    "title": r[0],
                    "address": r[1],
                    "confirmed": r[2],
                    "cancelled": r[3],
                    "count": r[4],
                    "property_id": r[5] if len(r) > 5 else None,
                }
                for r in rows
            ]
            # Merge with property inquiries from events (photo requests, etc.)
            inquiry_rows = conn.execute(
                """SELECT property, COUNT(*) as cnt
                   FROM events
                   WHERE property IS NOT NULL AND property != ''
                     AND event_type = 'property_inquiry'
                     AND created_at >= ?
                   GROUP BY property
                   ORDER BY cnt DESC
                   LIMIT 10""",
                (cutoff,),
            ).fetchall()
            # Merge inquiry counts into existing visit-based items
            existing_titles = {item["title"] for item in top_property_items}
            for r in inquiry_rows:
                title, cnt = r[0], r[1]
                found = False
                for item in top_property_items:
                    if item["title"] == title:
                        item["count"] += cnt
                        item["confirmed"] += cnt
                        found = True
                        break
                if not found:
                    top_property_items.append(
                        {"title": title, "address": "", "confirmed": cnt, "cancelled": 0, "count": cnt}
                    )
            # Enrich titles and addresses from current listings
            try:
                listings = sheets.get_listings()
                # Build id→listing map for quick lookup
                _id_map = {}
                for p in listings:
                    pid = str(p.get("id") or "").strip()
                    if pid:
                        _id_map[pid] = p
                for it in top_property_items:
                    # Try by property_id first, then fuzzy match
                    match = None
                    if it.get("property_id") and it["property_id"] in _id_map:
                        match = _id_map[it["property_id"]]
                    else:
                        match = _match_listing(it["title"], listings)
                    if match:
                        cur_title = (match.get("titulo") or "").strip()
                        cur_addr = str(match.get("direccion") or "").strip()
                        if cur_title:
                            it["title"] = cur_title
                        if cur_addr and cur_addr != "Consultar":
                            it["address"] = cur_addr
            except Exception:
                pass
            # Re-sort by count
            top_property_items.sort(key=lambda x: x["count"], reverse=True)
            top_property_items = top_property_items[:10]
            top_properties = {
                "labels": [item["title"] for item in top_property_items],
                "values": [item["count"] for item in top_property_items],
                "confirmed": [item["confirmed"] for item in top_property_items],
                "cancelled": [item["cancelled"] for item in top_property_items],
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
            # Count conversations where a human agent replied OR callback was requested
            escalated = conn.execute(
                """SELECT COUNT(DISTINCT sub.phone_hash) FROM (
                     SELECT cm.phone_hash FROM chat_messages cm
                       INNER JOIN conversations c ON cm.phone_hash = c.phone_hash
                       WHERE cm.role = 'agent' AND c.last_seen_at >= ?
                     UNION
                     SELECT e.phone_hash FROM events e
                       WHERE e.event_type = 'callback_requested' AND e.created_at >= ?
                   ) sub""",
                (cutoff, cutoff),
            ).fetchone()[0]
            bot_resolved = max(total_convs - escalated, 0)
            escalation_split = {
                "labels": ["Resueltas por bot", "Escaladas a humano"],
                "values": [bot_resolved, escalated],
            }

            # --- Lead quality split (filtered) ---
            # Use leads table for more accurate count
            leads_qualified = total_leads  # already computed above
            no_interaction = conn.execute(
                "SELECT COUNT(*) FROM conversations WHERE message_count <= 2 AND last_seen_at >= ?",
                (cutoff,),
            ).fetchone()[0]
            cold = max(total_convs - leads_qualified - no_interaction, 0)
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
                   WHERE event_type IN ('visit_scheduled', 'visit_request_notified')
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

            # --- Average first response time (seconds) ---
            avg_response_rows = conn.execute(
                """SELECT
                    cm1.phone,
                    MIN(CASE WHEN cm1.role = 'user' THEN cm1.created_at END) as first_user,
                    MIN(CASE WHEN cm1.role = 'assistant' THEN cm1.created_at END) as first_bot
                   FROM chat_messages cm1
                   INNER JOIN conversations c ON cm1.phone_hash = c.phone_hash
                   WHERE c.last_seen_at >= ?
                   GROUP BY cm1.phone
                   HAVING MIN(CASE WHEN cm1.role = 'user' THEN cm1.created_at END) IS NOT NULL
                      AND MIN(CASE WHEN cm1.role = 'assistant' THEN cm1.created_at END) IS NOT NULL""",
                (cutoff,),
            ).fetchall()

            response_times = []
            for row in avg_response_rows:
                try:
                    t_user = datetime.strptime(row[1], "%Y-%m-%dT%H:%M:%S")
                    t_bot = datetime.strptime(row[2], "%Y-%m-%dT%H:%M:%S")
                    delta = (t_bot - t_user).total_seconds()
                    if 0 < delta < 86400:  # Sanity: between 0 and 24h
                        response_times.append(delta)
                except (ValueError, TypeError):
                    pass

            avg_response_sec = round(sum(response_times) / len(response_times)) if response_times else 0

        return {
            "kpis": {
                "total_conversations": total_convs,
                "total_leads": total_leads,
                "total_visits": int(total_visits),
                "conv_to_lead_pct": conv_to_lead,
                "conv_to_visit_pct": conv_to_visit,
                "avg_response_sec": avg_response_sec,
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
                for col in ("name", "operation", "property_type", "budget", "timeline", "channel"):
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
                "SELECT name, operation, property_type, budget, timeline, notified, channel FROM leads WHERE phone = ?",
                (phone,),
            ).fetchone()
        if row is None:
            return None
        return {
            "name": row[0], "operation": row[1], "property_type": row[2],
            "budget": row[3], "timeline": row[4], "notified": bool(row[5]),
            "channel": row[6],
        }
    except Exception as e:
        logger.error("analytics.load_lead error: %s", e)
        return None


def set_agent_takeover(phone_hash: str, until_ts: float):
    """Persist agent takeover timestamp to DB."""
    try:
        until_str = datetime.fromtimestamp(until_ts, tz=AR_TZ).strftime("%Y-%m-%dT%H:%M:%S")
        with _db_lock:
            conn = _get_conn()
            cur = conn.execute(
                "UPDATE conversations SET agent_takeover_until = ? WHERE phone_hash = ?",
                (until_str, phone_hash),
            )
            if cur.rowcount == 0:
                now = datetime.now(AR_TZ).strftime("%Y-%m-%dT%H:%M:%S")
                conn.execute(
                    """INSERT INTO conversations
                       (phone_hash, channel, first_seen_at, last_seen_at, message_count,
                        became_lead, visit_count, operation, property_type, agent_takeover_until)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (phone_hash, "whatsapp", now, now, 0, 0, 0, None, None, until_str),
                )
    except Exception as e:
        logger.error("analytics.set_agent_takeover error: %s", e)


def clear_agent_takeover(phone_hash: str):
    """Clear agent takeover in DB."""
    try:
        with _db_lock:
            conn = _get_conn()
            conn.execute(
                "UPDATE conversations SET agent_takeover_until = NULL WHERE phone_hash = ?",
                (phone_hash,),
            )
    except Exception as e:
        logger.error("analytics.clear_agent_takeover error: %s", e)


def load_agent_takeover(phone_hash: str) -> float | None:
    """Load agent takeover timestamp from DB. Returns unix timestamp or None."""
    try:
        with _db_lock:
            conn = _get_conn()
            row = conn.execute(
                "SELECT agent_takeover_until FROM conversations WHERE phone_hash = ?",
                (phone_hash,),
            ).fetchone()
        if row and row[0]:
            from pytz import timezone as _tz
            dt = datetime.strptime(row[0], "%Y-%m-%dT%H:%M:%S")
            dt = AR_TZ.localize(dt)
            return dt.timestamp()
        return None
    except Exception as e:
        logger.error("analytics.load_agent_takeover error: %s", e)
        return None


def save_visit(phone: str, property_title: str, address: str, client_name: str,
               date_str: str, time_str: str, event_id: str | None = None,
               channel: str = "whatsapp", property_id: str | None = None):
    """Insert a visit record."""
    try:
        with _db_lock:
            conn = _get_conn()
            existing = conn.execute(
                """SELECT status FROM visits
                   WHERE phone = ? AND property_title = ? AND visit_date = ? AND visit_time = ?
                   ORDER BY id DESC LIMIT 1""",
                (phone, property_title, date_str, time_str),
            ).fetchone()
            if existing and existing[0] == "confirmed":
                return
            now = datetime.now(AR_TZ).strftime("%Y-%m-%dT%H:%M:%S")
            conn.execute(
                """INSERT INTO visits (phone, phone_hash, client_name, property_title,
                   address, visit_date, visit_time, calendar_event_id, status, channel,
                   property_id, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (phone, _hash_phone(phone), client_name or None, property_title,
                 address or None, date_str, time_str, event_id, "confirmed",
                 channel, property_id, now, now),
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


def get_visit_by_key(phone: str, property_title: str, date_str: str, time_str: str) -> dict | None:
    """Fetch a visit by key (phone + property + date + time)."""
    try:
        with _db_lock:
            conn = _get_conn()
            row = conn.execute(
                """SELECT id, status, calendar_event_id, created_at, updated_at
                   FROM visits
                   WHERE phone = ? AND property_title = ? AND visit_date = ? AND visit_time = ?
                   ORDER BY id DESC LIMIT 1""",
                (phone, property_title, date_str, time_str),
            ).fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "status": row[1],
            "calendar_event_id": row[2],
            "created_at": row[3],
            "updated_at": row[4],
        }
    except Exception as e:
        logger.error("analytics.get_visit_by_key error: %s", e)
        return None


def update_visit_event_id(phone: str, property_title: str, date_str: str, time_str: str, event_id: str) -> bool:
    """Update calendar_event_id for a visit by key."""
    try:
        with _db_lock:
            conn = _get_conn()
            now = datetime.now(AR_TZ).strftime("%Y-%m-%dT%H:%M:%S")
            cur = conn.execute(
                """UPDATE visits SET calendar_event_id = ?, updated_at = ?
                   WHERE phone = ? AND property_title = ? AND visit_date = ? AND visit_time = ?""",
                (event_id, now, phone, property_title, date_str, time_str),
            )
        return cur.rowcount > 0
    except Exception as e:
        logger.error("analytics.update_visit_event_id error: %s", e)
        return False


def acquire_lock(name: str, ttl_seconds: int = 600, owner: str = "") -> bool:
    """Acquire a simple distributed lock backed by the DB."""
    try:
        with _db_lock:
            conn = _get_conn()
            now = int(datetime.now(AR_TZ).timestamp())
            owner = owner or str(os.getpid())
            if _USE_PG:
                cur = conn.execute(
                    "INSERT INTO locks (name, acquired_at, owner) VALUES (?, ?, ?) ON CONFLICT(name) DO NOTHING",
                    (name, now, owner),
                )
            else:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO locks (name, acquired_at, owner) VALUES (?, ?, ?)",
                    (name, now, owner),
                )
            if cur.rowcount:
                return True
            row = conn.execute(
                "SELECT acquired_at FROM locks WHERE name = ?",
                (name,),
            ).fetchone()
            if not row:
                return False
            acquired_at = int(row[0])
            if now - acquired_at < ttl_seconds:
                return False
            cur = conn.execute(
                "UPDATE locks SET acquired_at = ?, owner = ? WHERE name = ? AND acquired_at = ?",
                (now, owner, name, acquired_at),
            )
            return cur.rowcount > 0
    except Exception as e:
        logger.error("analytics.acquire_lock error: %s", e)
        return False


def has_recent_event(phone: str, event_type: str, days: int = 30) -> bool:
    """Return True if a matching event exists within the last N days."""
    try:
        with _db_lock:
            conn = _get_conn()
            cutoff = (datetime.now(AR_TZ) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
            phone_hash = _hash_phone(phone)
            row = conn.execute(
                """SELECT 1 FROM events
                   WHERE event_type = ? AND phone_hash = ? AND created_at >= ?
                   LIMIT 1""",
                (event_type, phone_hash, cutoff),
            ).fetchone()
        return bool(row)
    except Exception as e:
        logger.error("analytics.has_recent_event error: %s", e)
        return False


def mark_message_processed(message_id: str, channel: str = "whatsapp") -> bool:
    """Idempotency guard. Returns True if inserted, False if already seen."""
    if not message_id:
        return True
    try:
        with _db_lock:
            conn = _get_conn()
            now = datetime.now(AR_TZ).strftime("%Y-%m-%dT%H:%M:%S")
            if _USE_PG:
                cur = conn.execute(
                    "INSERT INTO processed_messages (message_id, channel, created_at) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                    (message_id, channel, now),
                )
            else:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO processed_messages (message_id, channel, created_at) VALUES (?, ?, ?)",
                    (message_id, channel, now),
                )
        return bool(cur.rowcount)
    except Exception as e:
        logger.error("analytics.mark_message_processed error: %s", e)
        return True


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
            # PostgreSQL requires all non-aggregated columns in GROUP BY
            base_query = f"""
                SELECT cm.phone,
                       MAX(cm.phone_hash) as phone_hash,
                       MAX(cm.channel) as channel,
                       MIN(cm.created_at) as first_msg,
                       MAX(cm.created_at) as last_msg,
                       COUNT(cm.id) as msg_count,
                       MAX(l.name) as name,
                       MAX(COALESCE(c.became_lead, 0)) as is_lead,
                       MAX(COALESCE(c.visit_count, 0)) as visits
                FROM chat_messages cm
                LEFT JOIN leads l ON cm.phone = l.phone
                LEFT JOIN conversations c ON cm.phone_hash = c.phone_hash
                WHERE {' AND '.join(where)}
                GROUP BY cm.phone
            """

            if status == "lead":
                base_query += " HAVING MAX(COALESCE(c.became_lead, 0)) = 1"
            elif status == "visit":
                base_query += " HAVING MAX(COALESCE(c.visit_count, 0)) > 0"

            # Count total
            count_q = f"SELECT COUNT(*) FROM ({base_query}) sub"
            total = conn.execute(count_q, params).fetchone()[0]

            # Get page
            query = f"{base_query} ORDER BY last_msg DESC LIMIT ? OFFSET ?"
            params.extend([per_page, (page - 1) * per_page])
            rows = conn.execute(query, params).fetchall()

            # Get last message preview for each
            items = []
            for r in rows:
                phone = r[0]
                phone_hash = r[1]
                last_row = conn.execute(
                    "SELECT content, role FROM chat_messages WHERE phone = ? ORDER BY id DESC LIMIT 1",
                    (phone,),
                ).fetchone()
                visit_interest = conn.execute(
                    """SELECT COUNT(*) FROM events
                       WHERE phone_hash = ?
                         AND event_type IN ('visit_scheduled', 'visit_request_notified')""",
                    (phone_hash,),
                ).fetchone()[0]
                items.append({
                    "phone": phone[:4] + "****" + phone[-4:] if len(phone) > 8 else phone,
                    "phone_hash": phone_hash,
                    "channel": r[2],
                    "first_message": r[3],
                    "last_message": r[4],
                    "message_count": r[5],
                    "name": r[6] or ("Contacto" if r[2] in ("instagram", "facebook") else ""),
                    "is_lead": bool(r[7]),
                    "visit_count": r[8],
                    "last_preview": (last_row[0][:80] + "...") if last_row and len(last_row[0]) > 80 else (last_row[0] if last_row else ""),
                    "last_role": last_row[1] if last_row else "",
                    "score": _lead_score(None, r[8], r[5], is_lead=bool(r[7]),
                                         has_visit_interest=visit_interest > 0),
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


def _lead_score(budget, visit_count, message_count, is_lead=True, has_visit_interest=False):
    """Return 'hot', 'warm', or 'cold' based on lead engagement signals."""
    if visit_count > 0 or has_visit_interest:
        return "hot"
    if is_lead or message_count >= 5:
        return "warm"
    return "cold"


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
                       COALESCE(c.message_count, 0), COALESCE(c.visit_count, 0),
                       (SELECT COUNT(*) FROM visits v WHERE v.phone_hash = l.phone_hash) +
                       (SELECT COUNT(*) FROM events e WHERE e.phone_hash = l.phone_hash
                        AND e.event_type IN ('visit_scheduled', 'visit_request_notified')) as visit_interest
                FROM leads l
                LEFT JOIN conversations c ON l.phone_hash = c.phone_hash
                WHERE {' AND '.join(where)}
                ORDER BY l.{sort_col} DESC
                LIMIT ? OFFSET ?
            """
            params.extend([per_page, (page - 1) * per_page])
            rows = conn.execute(query, params).fetchall()

        now = datetime.now(AR_TZ)
        items = []
        for r in rows:
            try:
                updated_dt = datetime.strptime(r[10], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=AR_TZ)
                days_since = (now - updated_dt).days
            except Exception:
                days_since = -1
            visit_interest = r[13] if len(r) > 13 else 0
            items.append({
                "phone": r[0][:4] + "****" + r[0][-4:] if len(r[0]) > 8 else r[0],
                "phone_hash": r[1], "name": r[2] or "", "operation": r[3] or "",
                "property_type": r[4] or "", "budget": r[5] or "", "timeline": r[6] or "",
                "notified": bool(r[7]), "channel": r[8], "created_at": r[9],
                "updated_at": r[10], "message_count": r[11], "visit_count": r[12],
                "days_since_contact": days_since,
                "score": _lead_score(r[5], r[12], r[11], has_visit_interest=visit_interest > 0),
            })
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
EXTRA_VIDEO_PRICE_ARS = int(os.environ.get("EXTRA_VIDEO_PRICE_ARS", "25000"))


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
            "extra_video_price_ars": EXTRA_VIDEO_PRICE_ARS,
        }
    except Exception as e:
        logger.error("analytics.get_media_usage error: %s", e)
        return {
            "month": month, "videos_used": 0, "videos_purchased": 0,
            "free_limit": FREE_VIDEOS_PER_MONTH, "total_allowed": FREE_VIDEOS_PER_MONTH,
            "remaining": FREE_VIDEOS_PER_MONTH, "extra_video_price_usd": EXTRA_VIDEO_PRICE_USD,
            "extra_video_price_ars": EXTRA_VIDEO_PRICE_ARS,
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


# ---------------------------------------------------------------------------
# Media jobs persistence
# ---------------------------------------------------------------------------

def save_media_job(job: dict):
    """Insert or update a media job in the DB."""
    try:
        now = datetime.now(AR_TZ).strftime("%Y-%m-%dT%H:%M:%S")
        with _db_lock:
            conn = _get_conn()
            _upsert_sql = """
                INSERT INTO media_jobs (id, type, status, progress, property, photo_count, prompt, result_path, result_url, error, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            if _USE_PG:
                _upsert_sql += """ ON CONFLICT (id) DO UPDATE SET
                    status=EXCLUDED.status, progress=EXCLUDED.progress,
                    result_path=EXCLUDED.result_path, result_url=EXCLUDED.result_url,
                    error=EXCLUDED.error, updated_at=EXCLUDED.updated_at"""
            else:
                _upsert_sql += """ ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status, progress=excluded.progress,
                    result_path=excluded.result_path, result_url=excluded.result_url,
                    error=excluded.error, updated_at=excluded.updated_at"""
            conn.execute(_upsert_sql, (
                job.get("id"), job.get("type", ""),
                job.get("status", "queued"), job.get("progress", ""),
                job.get("property", ""), job.get("photo_count", 0),
                job.get("prompt", ""),
                job.get("result_path"), job.get("result_url"),
                job.get("error"),
                job.get("created_at", now), now,
            ))
    except Exception as e:
        logger.error("analytics.save_media_job error: %s", e)


def list_media_jobs(days: int = 7) -> list[dict]:
    """Return media jobs from the last N days, newest first."""
    try:
        cutoff = (datetime.now(AR_TZ) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
        with _db_lock:
            conn = _get_conn()
            rows = conn.execute(
                """SELECT id, type, status, progress, property, photo_count,
                          result_path, result_url, error, created_at
                   FROM media_jobs WHERE created_at >= ?
                   ORDER BY created_at DESC""",
                (cutoff,),
            ).fetchall()
        return [
            {"id": r[0], "type": r[1], "status": r[2], "progress": r[3],
             "property": r[4], "photo_count": r[5],
             "result_path": r[6], "result_url": r[7], "error": r[8],
             "created_at": r[9]}
            for r in rows
        ]
    except Exception as e:
        logger.error("analytics.list_media_jobs error: %s", e)
        return []


def get_media_job(job_id: str) -> dict | None:
    """Return a single media job by ID."""
    try:
        with _db_lock:
            conn = _get_conn()
            row = conn.execute(
                """SELECT id, type, status, progress, property, photo_count,
                          result_path, result_url, error, created_at
                   FROM media_jobs WHERE id = ?""",
                (job_id,),
            ).fetchone()
        if not row:
            return None
        return {"id": row[0], "type": row[1], "status": row[2], "progress": row[3],
                "property": row[4], "photo_count": row[5],
                "result_path": row[6], "result_url": row[7], "error": row[8],
                "created_at": row[9]}
    except Exception as e:
        logger.error("analytics.get_media_job error: %s", e)
        return None


def cleanup_old_media_jobs(days: int = 7):
    """Delete jobs older than N days and their files."""
    try:
        cutoff = (datetime.now(AR_TZ) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
        with _db_lock:
            conn = _get_conn()
            rows = conn.execute(
                "SELECT result_path FROM media_jobs WHERE created_at < ?",
                (cutoff,),
            ).fetchall()
            for row in rows:
                if row[0]:
                    try:
                        os.unlink(row[0])
                    except OSError:
                        pass
            conn.execute("DELETE FROM media_jobs WHERE created_at < ?", (cutoff,))
        logger.info("Cleaned up media jobs older than %d days", days)
    except Exception as e:
        logger.error("analytics.cleanup_old_media_jobs error: %s", e)


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------

def record_payment(
    payment_id: str,
    provider: str = "mercadopago",
    status: str = "pending",
    amount: float = 0,
    currency: str = "ARS",
    video_count: int = 1,
    payer_email: str = "",
    external_ref: str = "",
):
    """Insert or update a payment record."""
    try:
        now = datetime.now(AR_TZ).strftime("%Y-%m-%dT%H:%M:%S")
        with _db_lock:
            conn = _get_conn()
            _upsert_sql = """
                INSERT INTO payments
                    (payment_id, provider, status, amount, currency, video_count,
                     payer_email, external_ref, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            if _USE_PG:
                _upsert_sql += """ ON CONFLICT (payment_id) DO UPDATE SET
                    status=EXCLUDED.status, amount=EXCLUDED.amount,
                    payer_email=EXCLUDED.payer_email, updated_at=EXCLUDED.updated_at"""
            else:
                _upsert_sql += """ ON CONFLICT(payment_id) DO UPDATE SET
                    status=excluded.status, amount=excluded.amount,
                    payer_email=excluded.payer_email, updated_at=excluded.updated_at"""
            conn.execute(_upsert_sql, (payment_id, provider, status, amount, currency, video_count,
                  payer_email, external_ref, now, now))
    except Exception as e:
        logger.error("analytics.record_payment error: %s", e)


def get_payment(payment_id: str) -> dict | None:
    """Get a single payment by ID."""
    try:
        with _db_lock:
            conn = _get_conn()
            row = conn.execute(
                """SELECT payment_id, provider, status, amount, currency,
                          video_count, payer_email, created_at
                   FROM payments WHERE payment_id = ?""",
                (payment_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "payment_id": row[0], "provider": row[1], "status": row[2],
            "amount": row[3], "currency": row[4], "video_count": row[5],
            "payer_email": row[6], "created_at": row[7],
        }
    except Exception as e:
        logger.error("analytics.get_payment error: %s", e)
        return None


def get_payments_list(limit: int = 50) -> list[dict]:
    """Return recent payments, newest first."""
    try:
        with _db_lock:
            conn = _get_conn()
            rows = conn.execute(
                """SELECT payment_id, provider, status, amount, currency,
                          video_count, payer_email, created_at
                   FROM payments ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            {
                "payment_id": r[0], "provider": r[1], "status": r[2],
                "amount": r[3], "currency": r[4], "video_count": r[5],
                "payer_email": r[6], "created_at": r[7],
            }
            for r in rows
        ]
    except Exception as e:
        logger.error("analytics.get_payments_list error: %s", e)
        return []
