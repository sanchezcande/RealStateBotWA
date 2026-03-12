"""
Main Flask app.
Handles Meta WhatsApp, Facebook Messenger, and Instagram Direct webhook messages.
"""
import logging
import json
import os
import re
import time
import threading
import requests
from flask import Flask, request, jsonify, render_template_string
from config import VERIFY_TOKEN, PAGE_ACCESS_TOKEN, DASHBOARD_PLAN, BRANCH_NAME
import analytics
import conversations
import ai
import lead_qualifier
import visit_scheduler
import whatsapp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
analytics.init_db()

# Deduplication: buffer rapid consecutive messages from the same number
# and combine them into a single AI call.
_pending: dict = {}   # phone -> {"texts": [...], "timer": Timer}
_pending_lock = threading.Lock()
DEBOUNCE_SECONDS = 5


# ---------------------------------------------------------------------------
# Webhook verification (GET) — required by Meta
# ---------------------------------------------------------------------------

@app.get("/webhook")
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        logger.info("Webhook verified successfully.")
        return challenge, 200

    logger.warning("Webhook verification failed. Token mismatch.")
    return "Forbidden", 403


# ---------------------------------------------------------------------------
# Incoming messages (POST)
# ---------------------------------------------------------------------------

@app.post("/webhook")
def receive_message():
    data = request.get_json(silent=True) or {}

    # Always return 200 quickly so Meta doesn't retry
    try:
        _process_payload(data)
    except Exception as e:
        logger.error("Error processing webhook payload: %s", e, exc_info=True)

    return jsonify({"status": "ok"}), 200


def _process_payload(data: dict):
    """Extract messages from Meta's webhook payload and handle each one."""
    entry_list = data.get("entry", [])
    for entry in entry_list:
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages", [])
            for msg in messages:
                _handle_message(msg)


def _handle_message(msg: dict):
    msg_type = msg.get("type")
    phone = msg.get("from")  # sender's WhatsApp number (no +)

    if not phone:
        return

    if msg_type == "text":
        text = msg["text"]["body"].strip()
        logger.info("Incoming message from %s: %s", phone, text)
        _enqueue(phone, text)
    elif msg_type in ("image", "audio", "video", "document"):
        _enqueue(phone, "[archivo recibido — solo proceso texto]")
    else:
        logger.info("Unsupported message type '%s' from %s", msg_type, phone)


def _enqueue(phone: str, text: str):
    """Buffer messages for DEBOUNCE_SECONDS, then fire a single combined reply."""
    with _pending_lock:
        if phone in _pending:
            _pending[phone]["timer"].cancel()
            _pending[phone]["texts"].append(text)
        else:
            _pending[phone] = {"texts": [text]}

        timer = threading.Timer(DEBOUNCE_SECONDS, _flush, args=[phone])
        _pending[phone]["timer"] = timer
        timer.start()


def _flush(phone: str):
    """Called after the debounce window — combine buffered texts and reply once."""
    with _pending_lock:
        if phone not in _pending:
            return
        texts = _pending.pop(phone)["texts"]

    combined = " / ".join(texts) if len(texts) > 1 else texts[0]
    if len(texts) > 1:
        logger.info("Combined %d messages from %s: %s", len(texts), phone, combined)
    try:
        _reply(phone, combined)
    except Exception as e:
        logger.error("Unhandled error in _reply for %s: %s", phone, e, exc_info=True)
        try:
            whatsapp.send_message(phone, "Lo siento, hubo un problema técnico. Por favor intentá de nuevo en unos segundos.")
        except Exception:
            pass


def _extract_operation(text: str):
    """Detect buying/renting intent directly from user message text."""
    t = text.lower()
    if any(w in t for w in ("alquil", "alquilar", "alquiler", "rentar", "renta")):
        return "alquilar"
    if any(w in t for w in ("comprar", "compra", "venta", "compro", "comprando")):
        return "comprar"
    return None


def _extract_property_type(text: str):
    """Detect property type mentioned by the user."""
    t = text.lower()
    if any(w in t for w in ("monoambiente", "mono")):
        return "monoambiente"
    if any(w in t for w in ("departamento", "depto", "dpto", "dept")):
        return "departamento"
    # "2 ambientes", "tres ambientes", etc. → departamento (common Argentine expression)
    if re.search(r'\b(?:un|dos|tres|cuatro|cinco|\d)\s*ambientes?\b', t):
        return "departamento"
    if any(w in t for w in ("casa", "chalet")):
        return "casa"
    if re.search(r'\bph\b', t) or "p.h" in t:
        return "PH"
    if any(w in t for w in ("local", "comercial")):
        return "local"
    if any(w in t for w in ("oficina",)):
        return "oficina"
    return None


def _extract_name(text: str):
    """Detect user's name from common Spanish self-introduction patterns."""
    patterns = [
        r"(?:soy|me llamo|mi nombre es|mi nombre:)\s+([A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]{1,20})",
        r"(?:habla|te escribe|les escribe|te contacta|de parte de|acá)\s+([A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]{1,20})",
        r"(?:les\s+habla|acá\s+habla)\s+([A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]{1,20})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return m.group(1).capitalize()
    return None


def _reply(phone: str, user_text: str):
    # Track new conversations and all incoming messages
    is_new = len(conversations.get_messages(phone)) == 0
    analytics.log_event("message_in", phone, channel="whatsapp")

    # Store user message
    conversations.add_message(phone, "user", user_text)

    # Extract operation and property type directly from user text
    operation = _extract_operation(user_text)
    if operation:
        current = conversations.get_lead(phone)
        if not current.get("operation"):
            conversations.update_lead(phone, operation=operation)
            logger.info("Operation extracted from user text for %s: %s", phone, operation)

    prop_type = _extract_property_type(user_text)
    if prop_type:
        current = conversations.get_lead(phone)
        if not current.get("property_type"):
            conversations.update_lead(phone, property_type=prop_type)
            logger.info("Property type extracted from user text for %s: %s", phone, prop_type)

    name = _extract_name(user_text)
    if name:
        current = conversations.get_lead(phone)
        if not current.get("name"):
            conversations.update_lead(phone, name=name)
            logger.info("Name extracted from user text for %s: %s", phone, name)

    if is_new:
        analytics.log_event("new_conversation", phone, channel="whatsapp",
                             operation=operation, property_type=prop_type)

    # Get full history for context
    history = conversations.get_messages(phone)

    # Call AI
    lead = conversations.get_lead(phone)
    ai_response = ai.get_reply(history, lead=lead)

    # Process lead qualification (extracts hidden tag, maybe notifies agent)
    clean_response = lead_qualifier.process(phone, ai_response, channel="whatsapp")

    # Process visit scheduling (extracts visit tag, creates calendar event)
    clean_response = visit_scheduler.process(phone, clean_response)

    # Remove forbidden opening punctuation the model sometimes adds
    clean_response = clean_response.replace("¿", "").replace("¡", "")

    # Safety net: strip re-introduction if conversation is already in progress
    history_after = conversations.get_messages(phone)
    if len(history_after) > 2:
        clean_response = re.sub(
            r'Hola[!.]?\s*[Ss]oy Valentina[,.]?\s*con\s+qui[eé]n\s+hablo[?.!]*\s*',
            '',
            clean_response
        ).strip()

    # Store assistant reply (clean version)
    conversations.add_message(phone, "assistant", clean_response)

    # Send reply to user
    whatsapp.send_message(phone, clean_response)


# ---------------------------------------------------------------------------
# Facebook Messenger / Instagram Direct support
# To enable: subscribe the webhook in Meta App Dashboard under Messenger and
# Instagram settings (subscribed_fields: messages, messaging_postbacks).
# ---------------------------------------------------------------------------

def _send_meta_message(recipient_id: str, text: str):
    """Send a reply via Meta Graph API (Facebook Messenger / Instagram Direct)."""
    if not PAGE_ACCESS_TOKEN:
        logger.warning("PAGE_ACCESS_TOKEN not set — cannot send Meta message.")
        return
    try:
        resp = requests.post(
            "https://graph.facebook.com/v19.0/me/messages",
            params={"access_token": PAGE_ACCESS_TOKEN},
            json={"recipient": {"id": recipient_id}, "message": {"text": text}},
            timeout=10,
        )
        if not resp.ok:
            logger.error("Meta send API error %s: %s", resp.status_code, resp.text)
    except Exception as e:
        logger.error("Failed to send Meta message: %s", e)


def _reply_meta(sender_id: str, user_text: str):
    """Run the AI pipeline for a Facebook/Instagram message and reply."""
    if DASHBOARD_PLAN == "starter":
        logger.info("Meta message ignored — Starter plan does not include FB/IG channels.")
        return
    is_new = len(conversations.get_messages(sender_id)) == 0
    analytics.log_event("message_in", sender_id, channel="meta")

    conversations.add_message(sender_id, "user", user_text)

    operation = _extract_operation(user_text)
    if operation:
        current = conversations.get_lead(sender_id)
        if not current.get("operation"):
            conversations.update_lead(sender_id, operation=operation)

    prop_type = _extract_property_type(user_text)
    if prop_type:
        current = conversations.get_lead(sender_id)
        if not current.get("property_type"):
            conversations.update_lead(sender_id, property_type=prop_type)

    name = _extract_name(user_text)
    if name:
        current = conversations.get_lead(sender_id)
        if not current.get("name"):
            conversations.update_lead(sender_id, name=name)

    if is_new:
        analytics.log_event("new_conversation", sender_id, channel="meta",
                             operation=operation, property_type=prop_type)

    history = conversations.get_messages(sender_id)
    lead = conversations.get_lead(sender_id)
    ai_response = ai.get_reply(history, lead=lead)

    clean_response = lead_qualifier.process(sender_id, ai_response, channel="meta")
    clean_response = visit_scheduler.process(sender_id, clean_response)
    clean_response = clean_response.replace("¿", "").replace("¡", "")

    history_after = conversations.get_messages(sender_id)
    if len(history_after) > 2:
        clean_response = re.sub(
            r'Hola[!.]?\s*[Ss]oy Valentina[,.]?\s*con\s+qui[eé]n\s+hablo[?.!]*\s*',
            '',
            clean_response,
        ).strip()

    conversations.add_message(sender_id, "assistant", clean_response)
    _send_meta_message(sender_id, clean_response)


@app.get("/webhook/meta")
def verify_meta_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        logger.info("Meta webhook verified.")
        return challenge, 200
    return "Forbidden", 403


@app.post("/webhook/meta")
def receive_meta_message():
    data = request.get_json(silent=True) or {}
    # object is "page" for Facebook Messenger, "instagram" for Instagram Direct
    obj_type = data.get("object", "")
    if obj_type not in ("page", "instagram"):
        return jsonify({"status": "ok"}), 200
    try:
        for entry in data.get("entry", []):
            for messaging in entry.get("messaging", []):
                sender_id = messaging.get("sender", {}).get("id")
                message = messaging.get("message", {})
                # Skip delivery/read receipts and echo messages
                if message.get("is_echo") or not message.get("text"):
                    continue
                # Deduplicate by message ID (Meta sometimes sends the same webhook twice)
                mid = message.get("mid", "")
                if mid:
                    with _processed_mids_lock:
                        if mid in _processed_mids:
                            logger.info("Duplicate Meta message ignored: %s", mid)
                            continue
                        _processed_mids.add(mid)
                        if len(_processed_mids) > 1000:
                            _processed_mids.clear()
                text = message["text"].strip()
                if sender_id and text:
                    logger.info("Meta (%s) message from %s: %s", obj_type, sender_id, text)
                    _enqueue_meta(sender_id, text)
    except Exception as e:
        logger.error("Error processing Meta webhook: %s", e, exc_info=True)
    return jsonify({"status": "ok"}), 200


# Separate pending dict for Meta channels to avoid collision with WhatsApp phone numbers
_pending_meta: dict = {}
_pending_meta_lock = threading.Lock()

# Deduplication: track already-processed Meta message IDs to avoid double responses
_processed_mids: set = set()
_processed_mids_lock = threading.Lock()


def _enqueue_meta(sender_id: str, text: str):
    with _pending_meta_lock:
        if sender_id in _pending_meta:
            _pending_meta[sender_id]["timer"].cancel()
            _pending_meta[sender_id]["texts"].append(text)
        else:
            _pending_meta[sender_id] = {"texts": [text]}
        timer = threading.Timer(DEBOUNCE_SECONDS, _flush_meta, args=[sender_id])
        _pending_meta[sender_id]["timer"] = timer
        timer.start()


def _flush_meta(sender_id: str):
    with _pending_meta_lock:
        if sender_id not in _pending_meta:
            return
        texts = _pending_meta.pop(sender_id)["texts"]
    combined = " / ".join(texts) if len(texts) > 1 else texts[0]
    try:
        _reply_meta(sender_id, combined)
    except Exception as e:
        logger.error("Unhandled error in _reply_meta for %s: %s", sender_id, e, exc_info=True)
        try:
            _send_meta_message(sender_id, "Lo siento, hubo un problema técnico. Por favor intentá de nuevo en unos segundos.")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Analytics dashboard
# ---------------------------------------------------------------------------

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Valentina{% if branch %} — {{ branch }}{% endif %} — Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #f0f2f5; color: #1a1a2e; padding: 24px; }
  h1 { font-size: 1.4rem; font-weight: 700; margin-bottom: 4px; }
  .subtitle { font-size: 0.85rem; color: #666; margin-bottom: 8px; display: flex;
              align-items: center; gap: 12px; flex-wrap: wrap; }
  .subtitle a { color: #2563eb; text-decoration: none; }
  .pill { display: inline-block; padding: 2px 10px; border-radius: 99px; font-size: 0.75rem;
          font-weight: 600; background: #e0e7ff; color: #3730a3; }
  .period-btns { display: flex; gap: 6px; margin-bottom: 20px; }
  .period-btns a { padding: 5px 14px; border-radius: 6px; font-size: 0.8rem; font-weight: 600;
                   background: #fff; color: #555; text-decoration: none;
                   box-shadow: 0 1px 3px rgba(0,0,0,.1); }
  .period-btns a.active { background: #2563eb; color: #fff; }
  .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
          gap: 14px; margin-bottom: 24px; }
  .kpi { background: #fff; border-radius: 12px; padding: 18px;
         box-shadow: 0 1px 4px rgba(0,0,0,.08); }
  .kpi .num { font-size: 1.9rem; font-weight: 800; color: #2563eb; line-height: 1; }
  .kpi .pct { font-size: 0.78rem; font-weight: 600; margin-top: 3px; }
  .kpi .pct.up { color: #10b981; } .kpi .pct.down { color: #ef4444; } .kpi .pct.neu { color: #888; }
  .kpi .label { font-size: 0.72rem; color: #888; margin-top: 6px; text-transform: uppercase;
                letter-spacing: .04em; }
  .charts { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
            gap: 18px; }
  .card { background: #fff; border-radius: 12px; padding: 18px;
          box-shadow: 0 1px 4px rgba(0,0,0,.08); }
  .card h2 { font-size: 0.8rem; text-transform: uppercase; letter-spacing: .05em;
             color: #555; margin-bottom: 14px; }
  canvas { max-height: 240px; }
  .no-data { color: #aaa; font-size: 0.85rem; padding: 36px 0; text-align: center; }
  .actions { margin: 20px 0 4px; display: flex; gap: 10px; flex-wrap: wrap; }
  .btn { padding: 7px 16px; border-radius: 7px; font-size: 0.82rem; font-weight: 600;
         text-decoration: none; border: none; cursor: pointer; }
  .btn-outline { background: #fff; color: #2563eb; box-shadow: 0 1px 3px rgba(0,0,0,.12); }
  @media print {
    .period-btns, .actions, .subtitle a { display: none !important; }
    body { background: #fff; padding: 12px; }
    .card { box-shadow: none; border: 1px solid #e5e7eb; break-inside: avoid; }
  }
</style>
</head>
<body>
<h1>Valentina{% if branch %} — {{ branch }}{% endif %}</h1>
<div class="subtitle">
  <span>Ultimos {{ days }} dias</span>
  <span class="pill">{{ plan | upper }}</span>
  <a href="?token={{ token }}&days={{ days }}">Actualizar</a>
</div>

<div class="period-btns">
  <a href="?token={{ token }}&days=7" class="{{ 'active' if days == 7 else '' }}">7 dias</a>
  <a href="?token={{ token }}&days=30" class="{{ 'active' if days == 30 else '' }}">30 dias</a>
  <a href="?token={{ token }}&days=90" class="{{ 'active' if days == 90 else '' }}">90 dias</a>
</div>

<div class="kpis">
  <div class="kpi">
    <div class="num">{{ kpis.total_conversations }}</div>
    {% set diff = kpis.total_conversations - period_comparison.prev_convs %}
    {% if plan == 'premium' %}
      <div class="pct {{ 'up' if diff >= 0 else 'down' }}">
        {{ '+' if diff >= 0 else '' }}{{ diff }} vs periodo anterior
      </div>
    {% endif %}
    <div class="label">Conversaciones</div>
  </div>
  <div class="kpi">
    <div class="num">{{ kpis.total_leads }}</div>
    <div class="pct neu">{{ kpis.conv_to_lead_pct }}% del total</div>
    <div class="label">Leads calificados</div>
  </div>
  <div class="kpi">
    <div class="num">{{ kpis.total_visits }}</div>
    <div class="pct neu">{{ kpis.conv_to_visit_pct }}% del total</div>
    <div class="label">Visitas agendadas</div>
  </div>
  {% if plan in ('pro', 'premium') %}
  <div class="kpi">
    <div class="num">{{ escalation_split.values[1] }}</div>
    <div class="pct neu">
      {% if kpis.total_conversations > 0 %}
        {{ ((escalation_split.values[1] / kpis.total_conversations) * 100) | round(1) }}% del total
      {% endif %}
    </div>
    <div class="label">Escaladas a humano</div>
  </div>
  {% endif %}
</div>

{% if plan == 'premium' %}
<div class="actions">
  <a class="btn btn-outline" href="/dashboard/export.csv?token={{ token }}&days={{ days }}">Exportar CSV</a>
  <button class="btn btn-outline" onclick="window.print()">Imprimir / PDF</button>
</div>
{% endif %}

<div class="charts">
  <div class="card" style="grid-column: 1 / -1;">
    <h2>Conversaciones nuevas por dia</h2>
    {% if conv_by_day.labels %}
    <canvas id="convChart"></canvas>
    {% else %}<p class="no-data">Sin datos todavia</p>{% endif %}
  </div>

  <div class="card">
    <h2>Horarios pico</h2>
    {% if peak_hours.values | sum > 0 %}
    <canvas id="hoursChart"></canvas>
    {% else %}<p class="no-data">Sin datos todavia</p>{% endif %}
  </div>

  <div class="card">
    <h2>Propiedades mas solicitadas</h2>
    {% if top_properties.labels %}
    <canvas id="propsChart"></canvas>
    {% else %}<p class="no-data">Sin visitas registradas todavia</p>{% endif %}
  </div>

  {% if plan in ('pro', 'premium') %}
  <div class="card">
    <h2>Resolucion de consultas</h2>
    {% if escalation_split.values | sum > 0 %}
    <canvas id="escalChart"></canvas>
    {% else %}<p class="no-data">Sin datos todavia</p>{% endif %}
  </div>

  {% if channel_split.labels | length > 1 %}
  <div class="card">
    <h2>Canal de contacto</h2>
    <canvas id="channelChart"></canvas>
  </div>
  {% endif %}
  {% endif %}

  <div class="card">
    <h2>Tipo de operacion</h2>
    {% if op_split.labels %}
    <canvas id="opChart"></canvas>
    {% else %}<p class="no-data">Sin datos todavia</p>{% endif %}
  </div>

  {% if plan == 'premium' %}
  <div class="card">
    <h2>Calidad de leads</h2>
    {% if lead_quality_split.values | sum > 0 %}
    <canvas id="leadQChart"></canvas>
    {% else %}<p class="no-data">Sin datos todavia</p>{% endif %}
  </div>
  {% endif %}
</div>

<script>
const CONV      = {{ conv_by_day | tojson }};
const HOURS     = {{ peak_hours | tojson }};
const PROPS     = {{ top_properties | tojson }};
const OPS       = {{ op_split | tojson }};
const CHANNELS  = {{ channel_split | tojson }};
const ESCAL     = {{ escalation_split | tojson }};
const LEADQ     = {{ lead_quality_split | tojson }};

const BLUE  = "rgba(37,99,235,0.85)";
const GREEN = "rgba(16,185,129,0.85)";
const PAL   = ["#2563eb","#10b981","#f59e0b","#ef4444","#8b5cf6","#06b6d4","#ec4899","#84cc16"];
const opts0 = { plugins: { legend: { display: false } } };
const scaleY = { scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } };
const scaleX = { scales: { x: { beginAtZero: true, ticks: { precision: 0 } } } };

if (CONV.labels.length)
  new Chart(document.getElementById("convChart"), {
    type: "line",
    data: { labels: CONV.labels, datasets: [{ label: "Conversaciones", data: CONV.values,
            borderColor: BLUE, backgroundColor: "rgba(37,99,235,0.08)", fill: true,
            tension: 0.3, pointRadius: 3 }] },
    options: { ...opts0, ...scaleY }
  });

if (HOURS.values.reduce((a,b)=>a+b,0) > 0)
  new Chart(document.getElementById("hoursChart"), {
    type: "bar",
    data: { labels: HOURS.labels, datasets: [{ data: HOURS.values, backgroundColor: GREEN }] },
    options: { ...opts0, ...scaleY }
  });

if (PROPS.labels.length)
  new Chart(document.getElementById("propsChart"), {
    type: "bar",
    data: { labels: PROPS.labels, datasets: [{ data: PROPS.values, backgroundColor: PAL }] },
    options: { indexAxis: "y", ...opts0, ...scaleX }
  });

if (OPS.labels.length)
  new Chart(document.getElementById("opChart"), {
    type: "doughnut",
    data: { labels: OPS.labels, datasets: [{ data: OPS.values, backgroundColor: PAL }] },
    options: { plugins: { legend: { position: "bottom" } } }
  });

if (document.getElementById("escalChart") && ESCAL.values.reduce((a,b)=>a+b,0) > 0)
  new Chart(document.getElementById("escalChart"), {
    type: "doughnut",
    data: { labels: ESCAL.labels, datasets: [{ data: ESCAL.values, backgroundColor: [GREEN, "#ef4444"] }] },
    options: { plugins: { legend: { position: "bottom" } } }
  });

if (document.getElementById("channelChart") && CHANNELS.labels.length > 1)
  new Chart(document.getElementById("channelChart"), {
    type: "doughnut",
    data: { labels: CHANNELS.labels, datasets: [{ data: CHANNELS.values, backgroundColor: PAL }] },
    options: { plugins: { legend: { position: "bottom" } } }
  });

if (document.getElementById("leadQChart") && LEADQ.values.reduce((a,b)=>a+b,0) > 0)
  new Chart(document.getElementById("leadQChart"), {
    type: "doughnut",
    data: { labels: LEADQ.labels, datasets: [{ data: LEADQ.values,
            backgroundColor: ["#10b981","#f59e0b","#e5e7eb"] }] },
    options: { plugins: { legend: { position: "bottom" } } }
  });
</script>
</body>
</html>"""

_UPGRADE_HTML = """<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><title>Valentina — Dashboard</title>
<style>body{font-family:-apple-system,sans-serif;display:flex;align-items:center;
justify-content:center;min-height:100vh;background:#f0f2f5;margin:0;}
.box{background:#fff;border-radius:16px;padding:48px 40px;text-align:center;
box-shadow:0 2px 12px rgba(0,0,0,.1);max-width:420px;}
h1{font-size:1.3rem;margin-bottom:8px;}p{color:#666;font-size:.9rem;line-height:1.5;}
</style></head>
<body><div class="box">
<h1>Dashboard no disponible</h1>
<p>El plan <strong>Starter</strong> no incluye acceso al dashboard.<br>
Comunicate con tu asesor para conocer los planes Pro y Premium.</p>
</div></body></html>"""


@app.get("/dashboard")
def dashboard():
    token = request.args.get("token", "")
    expected = os.environ.get("DASHBOARD_TOKEN", "")
    if not expected or token != expected:
        return "Acceso denegado.", 403
    if DASHBOARD_PLAN == "starter":
        return _UPGRADE_HTML, 200
    days = int(request.args.get("days", 30))
    if days not in (7, 30, 90):
        days = 30
    data = analytics.get_dashboard_data(days=days)
    if not data:
        return "Error cargando datos.", 500
    return render_template_string(
        _DASHBOARD_HTML,
        token=token,
        plan=DASHBOARD_PLAN,
        branch=BRANCH_NAME,
        days=days,
        kpis=data["kpis"],
        conv_by_day=data["conv_by_day"],
        peak_hours=data["peak_hours"],
        top_properties=data["top_properties"],
        op_split=data["op_split"],
        channel_split=data["channel_split"],
        escalation_split=data["escalation_split"],
        lead_quality_split=data["lead_quality_split"],
        period_comparison=data["period_comparison"],
    )


@app.get("/dashboard/export.csv")
def dashboard_export_csv():
    import csv
    import io
    token = request.args.get("token", "")
    expected = os.environ.get("DASHBOARD_TOKEN", "")
    if not expected or token != expected:
        return "Acceso denegado.", 403
    if DASHBOARD_PLAN != "premium":
        return "Exportacion CSV disponible solo en el plan Premium.", 403
    days = int(request.args.get("days", 30))
    if days not in (7, 30, 90):
        days = 30
    try:
        import sqlite3 as _sqlite3
        db_path = os.environ.get("ANALYTICS_DB_PATH", "analytics.db")
        conn = _sqlite3.connect(db_path, check_same_thread=False)
        rows = conn.execute(
            """SELECT phone_hash, channel, first_seen_at, last_seen_at,
                      message_count, became_lead, visit_count, operation, property_type
               FROM conversations
               WHERE last_seen_at >= DATE('now', ?)
               ORDER BY last_seen_at DESC""",
            (f"-{days} days",),
        ).fetchall()
        conn.close()
    except Exception as e:
        logger.error("CSV export error: %s", e)
        return "Error generando el CSV.", 500

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id_anonimo", "canal", "primer_contacto", "ultimo_contacto",
                     "mensajes", "lead_calificado", "visitas", "operacion", "tipo_propiedad"])
    writer.writerows(rows)
    branch_slug = BRANCH_NAME.lower().replace(" ", "_") if BRANCH_NAME else "valentina"
    filename = f"{branch_slug}_leads_{days}d.csv"
    return output.getvalue(), 200, {
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": f"attachment; filename={filename}",
    }


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
