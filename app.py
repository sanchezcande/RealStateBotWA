"""
Main Flask app.
Handles Meta WhatsApp webhook verification and incoming message processing.
"""
import logging
import json
import time
import threading
from flask import Flask, request, jsonify
from config import VERIFY_TOKEN
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

# Deduplication: buffer rapid consecutive messages from the same number
# and combine them into a single AI call.
_pending: dict = {}   # phone -> {"texts": [...], "timer": Timer}
_pending_lock = threading.Lock()
DEBOUNCE_SECONDS = 3


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
    _reply(phone, combined)


def _extract_operation(text: str):
    """Detect buying/renting intent directly from user message text."""
    t = text.lower()
    if any(w in t for w in ("alquil", "alquilar", "alquiler", "rentar", "renta")):
        return "alquilar"
    if any(w in t for w in ("comprar", "compra", "venta", "compro", "comprando")):
        return "comprar"
    return None


def _reply(phone: str, user_text: str):
    # Store user message
    conversations.add_message(phone, "user", user_text)

    # Extract operation directly from user text (don't rely solely on AI tag)
    operation = _extract_operation(user_text)
    if operation:
        current = conversations.get_lead(phone)
        if not current.get("operation"):
            conversations.update_lead(phone, operation=operation)
            logger.info("Operation extracted from user text for %s: %s", phone, operation)

    # Get full history for context
    history = conversations.get_messages(phone)

    # Call AI
    lead = conversations.get_lead(phone)
    ai_response = ai.get_reply(history, lead=lead)

    # Process lead qualification (extracts hidden tag, maybe notifies agent)
    clean_response = lead_qualifier.process(phone, ai_response)

    # Process visit scheduling (extracts visit tag, creates calendar event)
    clean_response = visit_scheduler.process(phone, clean_response)

    # Store assistant reply (clean version)
    conversations.add_message(phone, "assistant", clean_response)

    # Send reply to user
    whatsapp.send_message(phone, clean_response)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
