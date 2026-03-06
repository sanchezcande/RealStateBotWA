"""
Main Flask app.
Handles Meta WhatsApp webhook verification and incoming message processing.
"""
import logging
import json
from flask import Flask, request, jsonify
from config import VERIFY_TOKEN
import conversations
import ai
import lead_qualifier
import whatsapp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)


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
        _reply(phone, text)
    elif msg_type in ("image", "audio", "video", "document"):
        _reply(phone, "Recibí tu archivo. Por el momento solo puedo procesar mensajes de texto. ¿En qué te puedo ayudar?")
    else:
        logger.info("Unsupported message type '%s' from %s", msg_type, phone)


def _reply(phone: str, user_text: str):
    # Store user message
    conversations.add_message(phone, "user", user_text)

    # Get full history for context
    history = conversations.get_messages(phone)

    # Call AI
    ai_response = ai.get_reply(history)

    # Process lead qualification (extracts hidden tag, maybe notifies agent)
    clean_response = lead_qualifier.process(phone, ai_response)

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
