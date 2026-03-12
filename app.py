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
from config import VERIFY_TOKEN, PAGE_ACCESS_TOKEN
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
<title>Valentina — Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #f0f2f5; color: #1a1a2e; padding: 24px; }
  h1 { font-size: 1.4rem; font-weight: 700; margin-bottom: 4px; }
  .subtitle { font-size: 0.85rem; color: #666; margin-bottom: 24px; }
  .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
          gap: 16px; margin-bottom: 28px; }
  .kpi { background: #fff; border-radius: 12px; padding: 20px;
         box-shadow: 0 1px 4px rgba(0,0,0,.08); }
  .kpi .num { font-size: 2rem; font-weight: 800; color: #2563eb; line-height: 1; }
  .kpi .pct { font-size: 0.8rem; color: #10b981; font-weight: 600; margin-top: 2px; }
  .kpi .label { font-size: 0.75rem; color: #888; margin-top: 6px; text-transform: uppercase;
                letter-spacing: .04em; }
  .charts { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
            gap: 20px; }
  .card { background: #fff; border-radius: 12px; padding: 20px;
          box-shadow: 0 1px 4px rgba(0,0,0,.08); }
  .card h2 { font-size: 0.85rem; text-transform: uppercase; letter-spacing: .05em;
             color: #555; margin-bottom: 16px; }
  canvas { max-height: 260px; }
  .no-data { color: #aaa; font-size: 0.85rem; padding: 40px 0; text-align: center; }
</style>
</head>
<body>
<h1>Valentina — Analytics</h1>
<p class="subtitle">Ultimos 30 dias &nbsp;·&nbsp; <a href="?token={{ token }}">Actualizar</a></p>

<div class="kpis">
  <div class="kpi">
    <div class="num">{{ kpis.total_conversations }}</div>
    <div class="label">Conversaciones</div>
  </div>
  <div class="kpi">
    <div class="num">{{ kpis.total_leads }}</div>
    <div class="pct">{{ kpis.conv_to_lead_pct }}% del total</div>
    <div class="label">Leads calificados</div>
  </div>
  <div class="kpi">
    <div class="num">{{ kpis.total_visits }}</div>
    <div class="pct">{{ kpis.conv_to_visit_pct }}% del total</div>
    <div class="label">Visitas agendadas</div>
  </div>
</div>

<div class="charts">
  <div class="card" style="grid-column: 1 / -1;">
    <h2>Conversaciones nuevas por dia</h2>
    {% if conv_by_day.labels %}
    <canvas id="convChart"></canvas>
    {% else %}<p class="no-data">Sin datos todavia</p>{% endif %}
  </div>
  <div class="card">
    <h2>Horarios pico (mensajes recibidos)</h2>
    {% if peak_hours.values | sum > 0 %}
    <canvas id="hoursChart"></canvas>
    {% else %}<p class="no-data">Sin datos todavia</p>{% endif %}
  </div>
  <div class="card">
    <h2>Propiedades mas solicitadas (visitas)</h2>
    {% if top_properties.labels %}
    <canvas id="propsChart"></canvas>
    {% else %}<p class="no-data">Sin visitas registradas todavia</p>{% endif %}
  </div>
  <div class="card">
    <h2>Tipo de operacion</h2>
    {% if op_split.labels %}
    <canvas id="opChart"></canvas>
    {% else %}<p class="no-data">Sin datos todavia</p>{% endif %}
  </div>
  {% if channel_split.labels | length > 1 %}
  <div class="card">
    <h2>Canal</h2>
    <canvas id="channelChart"></canvas>
  </div>
  {% endif %}
</div>

<script>
const CONV     = {{ conv_by_day | tojson }};
const HOURS    = {{ peak_hours | tojson }};
const PROPS    = {{ top_properties | tojson }};
const OPS      = {{ op_split | tojson }};
const CHANNELS = {{ channel_split | tojson }};

const BLUE  = "rgba(37,99,235,0.85)";
const GREEN = "rgba(16,185,129,0.85)";
const PAL   = ["#2563eb","#10b981","#f59e0b","#ef4444","#8b5cf6","#06b6d4","#ec4899","#84cc16"];

if (CONV.labels.length) {
  new Chart(document.getElementById("convChart"), {
    type: "line",
    data: { labels: CONV.labels, datasets: [{ label: "Conversaciones", data: CONV.values,
            borderColor: BLUE, backgroundColor: "rgba(37,99,235,0.1)", fill: true,
            tension: 0.3, pointRadius: 3 }] },
    options: { plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } }
  });
}

if (HOURS.values.reduce((a,b)=>a+b,0) > 0) {
  new Chart(document.getElementById("hoursChart"), {
    type: "bar",
    data: { labels: HOURS.labels, datasets: [{ label: "Mensajes", data: HOURS.values,
            backgroundColor: GREEN }] },
    options: { plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } }
  });
}

if (PROPS.labels.length) {
  new Chart(document.getElementById("propsChart"), {
    type: "bar",
    data: { labels: PROPS.labels, datasets: [{ label: "Visitas", data: PROPS.values,
            backgroundColor: PAL }] },
    options: { indexAxis: "y", plugins: { legend: { display: false } },
               scales: { x: { beginAtZero: true, ticks: { precision: 0 } } } }
  });
}

if (OPS.labels.length) {
  new Chart(document.getElementById("opChart"), {
    type: "doughnut",
    data: { labels: OPS.labels, datasets: [{ data: OPS.values, backgroundColor: PAL }] },
    options: { plugins: { legend: { position: "bottom" } } }
  });
}

if (CHANNELS.labels.length > 1) {
  new Chart(document.getElementById("channelChart"), {
    type: "doughnut",
    data: { labels: CHANNELS.labels, datasets: [{ data: CHANNELS.values, backgroundColor: PAL }] },
    options: { plugins: { legend: { position: "bottom" } } }
  });
}
</script>
</body>
</html>"""


@app.get("/dashboard")
def dashboard():
    token = request.args.get("token", "")
    expected = os.environ.get("DASHBOARD_TOKEN", "")
    if not expected or token != expected:
        return "Acceso denegado.", 403
    data = analytics.get_dashboard_data(days=30)
    if not data:
        return "Error cargando datos.", 500
    return render_template_string(
        _DASHBOARD_HTML,
        token=token,
        kpis=data["kpis"],
        conv_by_day=data["conv_by_day"],
        peak_hours=data["peak_hours"],
        top_properties=data["top_properties"],
        op_split=data["op_split"],
        channel_split=data["channel_split"],
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
