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
from flask import Flask, request, jsonify, render_template
from config import VERIFY_TOKEN, PAGE_ACCESS_TOKEN, DASHBOARD_PLAN, DASHBOARD_SECRET_KEY, ASSET_VERSION, PHONE_NUMBER_ID
import analytics
import conversations
import ai
import lead_qualifier
import visit_scheduler
import whatsapp
import drive_photos

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = DASHBOARD_SECRET_KEY
app.jinja_env.globals["v"] = ASSET_VERSION
analytics.init_db()
import followup
followup.start()

# Register dashboard blueprints
from dashboard_routes import dashboard as dashboard_bp
from dashboard_api import api as dashboard_api_bp
from payments import payments_bp
app.register_blueprint(dashboard_bp)
app.register_blueprint(dashboard_api_bp)
app.register_blueprint(payments_bp)

# Landing page
@app.route("/")
def landing():
    return render_template("landing.html")

@app.route("/privacy")
def privacy():
    return render_template("privacy.html")

# Serve uploaded media files
from config import MEDIA_UPLOAD_DIR
@app.route("/uploads/<path:filename>")
def serve_upload(filename):
    from flask import send_from_directory
    return send_from_directory(MEDIA_UPLOAD_DIR, filename)

# Deduplication: buffer rapid consecutive messages from the same number
# and combine them into a single AI call.
_pending: dict = {}   # phone -> {"texts": [...], "timer": Timer}
_pending_lock = threading.Lock()
DEBOUNCE_SECONDS = 5
MAX_MESSAGE_LENGTH = 4000


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
    logger.info("Webhook POST received: %s", json.dumps(data)[:500])

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
        text = (msg.get("text") or {}).get("body", "").strip()
        if not text:
            return
        logger.info("Incoming message from %s: %s", phone, text)
        _enqueue(phone, text)
        return

    if msg_type == "interactive":
        text = _extract_interactive_text(msg)
        if text:
            logger.info("Incoming interactive message from %s: %s", phone, text)
            _enqueue(phone, text)
            return
        logger.info("Interactive message without text from %s", phone)
        return

    if msg_type == "button":
        text = (msg.get("button") or {}).get("text", "").strip()
        if text:
            logger.info("Incoming button message from %s: %s", phone, text)
            _enqueue(phone, text)
            return
        logger.info("Button message without text from %s", phone)
        return

    if msg_type == "audio":
        media_id = (msg.get("audio") or {}).get("id")
        if media_id:
            threading.Thread(
                target=_transcribe_and_enqueue,
                args=(phone, media_id),
                daemon=True,
            ).start()
        else:
            _enqueue(phone, "[audio recibido — no se pudo procesar]")
        return

    if msg_type in ("image", "video", "document"):
        _enqueue(phone, "[archivo recibido — solo proceso texto]")
        return

    logger.info("Unsupported message type '%s' from %s", msg_type, phone)


def _extract_interactive_text(msg: dict) -> str:
    """Extract user-visible text from WhatsApp interactive payloads."""
    interactive = msg.get("interactive") or {}
    i_type = interactive.get("type")
    if i_type == "button_reply":
        payload = interactive.get("button_reply") or {}
        return (payload.get("title") or payload.get("id") or "").strip()
    if i_type == "list_reply":
        payload = interactive.get("list_reply") or {}
        return (payload.get("title") or payload.get("description") or payload.get("id") or "").strip()
    return ""


def _download_whatsapp_media(media_id: str) -> bytes | None:
    """Download media from WhatsApp Cloud API by media ID."""
    token = os.environ.get("WHATSAPP_TOKEN", "")
    if not token:
        from config import WHATSAPP_TOKEN
        token = WHATSAPP_TOKEN
    headers = {"Authorization": f"Bearer {token}"}
    try:
        # Step 1: get the download URL
        resp = requests.get(
            f"https://graph.facebook.com/v19.0/{media_id}",
            headers=headers, timeout=10,
        )
        resp.raise_for_status()
        url = resp.json().get("url")
        if not url:
            return None
        # Step 2: download the binary
        media_resp = requests.get(url, headers=headers, timeout=30)
        media_resp.raise_for_status()
        return media_resp.content
    except Exception as e:
        logger.error("Failed to download WhatsApp media %s: %s", media_id, e)
        return None


def _transcribe_audio_gemini(audio_bytes: bytes, mime_type: str = "audio/ogg") -> str | None:
    """Transcribe audio using Google Gemini."""
    from config import GOOGLE_AI_API_KEY
    key = os.environ.get("GOOGLE_AI_API_KEY", "") or GOOGLE_AI_API_KEY
    if not key:
        logger.warning("GOOGLE_AI_API_KEY not set — cannot transcribe audio")
        return None
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=key)
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[
                types.Content(parts=[
                    types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
                    types.Part.from_text(
                        "Transcribi este audio de WhatsApp a texto, palabra por palabra. "
                        "Solo devolvé la transcripcion, sin explicaciones ni comentarios."
                    ),
                ]),
            ],
        )
        text = (response.text or "").strip()
        return text if text else None
    except Exception as e:
        logger.error("Gemini audio transcription failed: %s", e)
        return None


def _transcribe_and_enqueue(phone: str, media_id: str):
    """Download WhatsApp audio, transcribe with Gemini, and enqueue as text."""
    try:
        audio_bytes = _download_whatsapp_media(media_id)
        if not audio_bytes:
            _enqueue(phone, "[audio recibido — no se pudo descargar]")
            return

        text = _transcribe_audio_gemini(audio_bytes, mime_type="audio/ogg")
        if not text:
            _enqueue(phone, "[audio recibido — no se pudo transcribir]")
            return

        logger.info("Audio transcribed for %s: %s", phone, text[:100])
        _enqueue(phone, text)
    except Exception as e:
        logger.error("Audio transcription pipeline failed for %s: %s", phone, e)
        _enqueue(phone, "[audio recibido — error al procesar]")


def _enqueue(phone: str, text: str):
    """Buffer messages for DEBOUNCE_SECONDS, then fire a single combined reply."""
    text = text[:MAX_MESSAGE_LENGTH]
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


def _process_reply(identifier: str, user_text: str, channel: str, send_fn,
                    send_image_fn=None):
    """Shared AI pipeline for all channels (WhatsApp, Facebook, Instagram)."""
    # Check if human agent has taken over this conversation
    if conversations.is_agent_takeover(identifier):
        conversations.add_message(identifier, "user", user_text, channel=channel)
        analytics.log_event("message_in", identifier, channel=channel)
        logger.info("AI paused for %s (agent takeover) — message stored, no auto-reply", identifier)
        return

    is_new = len(conversations.get_messages(identifier)) == 0
    analytics.log_event("message_in", identifier, channel=channel)
    conversations.add_message(identifier, "user", user_text, channel=channel)

    # Extract operation, property type, and name from user text
    operation = _extract_operation(user_text)
    if operation:
        current = conversations.get_lead(identifier)
        if not current.get("operation"):
            conversations.update_lead(identifier, operation=operation)
            logger.info("Operation extracted for %s: %s", identifier, operation)

    prop_type = _extract_property_type(user_text)
    if prop_type:
        current = conversations.get_lead(identifier)
        if not current.get("property_type"):
            conversations.update_lead(identifier, property_type=prop_type)
            logger.info("Property type extracted for %s: %s", identifier, prop_type)

    name = _extract_name(user_text)
    if name:
        current = conversations.get_lead(identifier)
        if not current.get("name"):
            conversations.update_lead(identifier, name=name)
            logger.info("Name extracted for %s: %s", identifier, name)

    if is_new:
        analytics.log_event("new_conversation", identifier, channel=channel,
                             operation=operation, property_type=prop_type)

    history = conversations.get_messages(identifier)
    lead = conversations.get_lead(identifier)
    ai_response = ai.get_reply(history, lead=lead)

    clean_response = lead_qualifier.process(identifier, ai_response, channel=channel)
    clean_response = visit_scheduler.process(identifier, clean_response)
    clean_response = clean_response.replace("¿", "").replace("¡", "")

    # Safety net: strip re-introduction if conversation is already in progress
    history_after = conversations.get_messages(identifier)
    if len(history_after) > 2:
        clean_response = re.sub(
            r'Hola[!.]?\s*[Ss]oy Vera[,.]?\s*con\s+qui[eé]n\s+hablo[?.!]*\s*',
            '',
            clean_response,
        ).strip()

    # Detect Drive URLs — download photos and send as images
    drive_urls = drive_photos.extract_drive_urls(clean_response)
    photos: list[tuple] = []
    if drive_urls and send_image_fn:
        photos = drive_photos.download_photos(drive_urls)
        if photos:
            clean_response = drive_photos.strip_drive_urls(clean_response)

    # Detect external image URLs (e.g. esquelprop.com) and download them
    ext_photos: list[tuple] = []
    if send_image_fn and not photos:
        _IMG_URL_RE = re.compile(r'(https?://[^\s)>\]]+\.(?:jpg|jpeg|png|webp)(?:\?[^\s)>\]]*)?)', re.IGNORECASE)
        img_urls = _IMG_URL_RE.findall(clean_response)
        for img_url in img_urls[:5]:
            try:
                r = requests.get(img_url, timeout=15)
                if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
                    mime = r.headers.get("content-type", "image/jpeg").split(";")[0]
                    ext_photos.append((img_url.split("/")[-1], r.content, mime))
                    clean_response = clean_response.replace(img_url, "").strip()
            except Exception as e:
                logger.warning("Failed to download external image %s: %s", img_url, e)
        clean_response = re.sub(r'\n\s*\n\s*\n', '\n\n', clean_response).strip()

    conversations.add_message(identifier, "assistant", clean_response, channel=channel)
    if clean_response:
        send_fn(identifier, clean_response)

    # Send downloaded photos as actual images
    for _name, img_data, mime in (photos or ext_photos):
        try:
            send_image_fn(identifier, img_data, mime)
            time.sleep(0.5)  # small delay between images
        except Exception as e:
            logger.error("Failed sending photo to %s: %s", identifier, e)


def _reply(phone: str, user_text: str):
    _process_reply(phone, user_text, "whatsapp", whatsapp.send_message,
                   send_image_fn=whatsapp.send_image)


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


def _send_meta_image(recipient_id: str, image_data: bytes, mime_type: str = "image/jpeg"):
    """Send an image via Meta Graph API (Facebook Messenger / Instagram Direct)."""
    if not PAGE_ACCESS_TOKEN:
        logger.warning("PAGE_ACCESS_TOKEN not set — cannot send Meta image.")
        return
    try:
        ext = mime_type.split("/")[-1].replace("jpeg", "jpg")
        resp = requests.post(
            "https://graph.facebook.com/v19.0/me/messages",
            params={"access_token": PAGE_ACCESS_TOKEN},
            data={
                "recipient": json.dumps({"id": recipient_id}),
                "message": json.dumps({
                    "attachment": {"type": "image", "payload": {}}
                }),
            },
            files={"filedata": (f"photo.{ext}", image_data, mime_type)},
            timeout=30,
        )
        if not resp.ok:
            logger.error("Meta image API error %s: %s", resp.status_code, resp.text)
    except Exception as e:
        logger.error("Failed to send Meta image: %s", e)


def _get_meta_profile_name(sender_id: str) -> str | None:
    """Fetch the user's first name from Meta Graph API (works for FB & IG)."""
    if not PAGE_ACCESS_TOKEN:
        return None
    try:
        resp = requests.get(
            f"https://graph.facebook.com/v19.0/{sender_id}",
            params={"fields": "first_name", "access_token": PAGE_ACCESS_TOKEN},
            timeout=5,
        )
        if resp.ok:
            return resp.json().get("first_name")
    except Exception as e:
        logger.warning("Could not fetch Meta profile for %s: %s", sender_id, e)
    return None


def _reply_meta(sender_id: str, user_text: str, channel: str):
    """Run the AI pipeline for a Facebook/Instagram message and reply."""
    if DASHBOARD_PLAN == "starter":
        logger.info("Meta message ignored — Starter plan does not include FB/IG channels.")
        return

    # Pre-load profile name from FB/IG so Vera doesn't need to ask
    current = conversations.get_lead(sender_id)
    if not current.get("name"):
        profile_name = _get_meta_profile_name(sender_id)
        if profile_name:
            conversations.update_lead(sender_id, name=profile_name)
            logger.info("Profile name pre-loaded for %s: %s", sender_id, profile_name)

    _process_reply(sender_id, user_text, channel, _send_meta_message,
                   send_image_fn=_send_meta_image)


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
                        _processed_mids[mid] = True
                        if len(_processed_mids) > 1000:
                            for _k in list(_processed_mids.keys())[:500]:
                                del _processed_mids[_k]
                text = message["text"].strip()
                if sender_id and text:
                    logger.info("Meta (%s) message from %s: %s", obj_type, sender_id, text)
                    channel = "facebook" if obj_type == "page" else "instagram"
                    _enqueue_meta(sender_id, text, channel)
    except Exception as e:
        logger.error("Error processing Meta webhook: %s", e, exc_info=True)
    return jsonify({"status": "ok"}), 200


# Separate pending dict for Meta channels to avoid collision with WhatsApp phone numbers
_pending_meta: dict = {}
_pending_meta_lock = threading.Lock()

# Deduplication: track already-processed Meta message IDs to avoid double responses
_processed_mids: dict = {}   # mid -> True; insertion-ordered for FIFO eviction
_processed_mids_lock = threading.Lock()


def _enqueue_meta(sender_id: str, text: str, channel: str):
    text = text[:MAX_MESSAGE_LENGTH]
    key = (channel, sender_id)
    with _pending_meta_lock:
        if key in _pending_meta:
            _pending_meta[key]["timer"].cancel()
            _pending_meta[key]["texts"].append(text)
        else:
            _pending_meta[key] = {"texts": [text], "channel": channel, "sender_id": sender_id}
        timer = threading.Timer(DEBOUNCE_SECONDS, _flush_meta, args=[key])
        _pending_meta[key]["timer"] = timer
        timer.start()


def _flush_meta(key):
    with _pending_meta_lock:
        if key not in _pending_meta:
            return
        payload = _pending_meta.pop(key)
        texts = payload["texts"]
        sender_id = payload["sender_id"]
        channel = payload["channel"]
    combined = " / ".join(texts) if len(texts) > 1 else texts[0]
    try:
        _reply_meta(sender_id, combined, channel)
    except Exception as e:
        logger.error("Unhandled error in _reply_meta for %s: %s", sender_id, e, exc_info=True)
        try:
            _send_meta_message(sender_id, "Lo siento, hubo un problema técnico. Por favor intentá de nuevo en unos segundos.")
        except Exception:
            pass




# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    checks = {"api": "ok"}
    checks["database"] = "ok" if analytics.health_check() else "error"
    all_ok = all(v == "ok" for v in checks.values())
    return jsonify({"status": "ok" if all_ok else "degraded", "checks": checks}), 200 if all_ok else 503


@app.get("/health/whatsapp")
def health_whatsapp():
    """Diagnose WhatsApp Cloud API phone number status and webhook config."""
    token = os.environ.get("WHATSAPP_TOKEN", "")
    phone_id = PHONE_NUMBER_ID
    result = {}
    # 1. Phone number info
    try:
        r = requests.get(
            f"https://graph.facebook.com/v21.0/{phone_id}",
            params={"fields": "display_phone_number,verified_name,quality_rating,platform_type,status,name_status,messaging_limit_tier,is_official_business_account,account_mode"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        result["phone"] = r.json()
    except Exception as e:
        result["phone"] = {"error": str(e)}
    # 2. WABA ID from phone number
    try:
        r = requests.get(
            f"https://graph.facebook.com/v21.0/{phone_id}/whatsapp_business_account",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        waba_data = r.json()
        result["waba"] = waba_data
        waba_id = waba_data.get("id")
        if waba_id:
            # 3. Check subscribed apps for this WABA
            r2 = requests.get(
                f"https://graph.facebook.com/v21.0/{waba_id}/subscribed_apps",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            result["subscribed_apps"] = r2.json()
            # 4. Try to subscribe the app (in case it's not subscribed)
            r3 = requests.post(
                f"https://graph.facebook.com/v21.0/{waba_id}/subscribed_apps",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            result["subscribe_result"] = r3.json()
    except Exception as e:
        result["waba"] = {"error": str(e)}
    return jsonify(result), 200


@app.get("/health/deepseek")
def health_deepseek():
    """
    Lightweight DeepSeek connectivity check.
    Does not consume tokens; attempts a low-cost request and reports status.
    """
    import socket
    from config import DEEPSEEK_BASE_URL

    host = DEEPSEEK_BASE_URL.replace("https://", "").replace("http://", "").split("/")[0]
    try:
        socket.gethostbyname(host)
    except Exception as e:
        return jsonify({"status": "error", "error": f"DNS failure: {e}"}), 503

    try:
        # Simple call with minimal payload; returns quickly if reachable.
        import ai
        resp = ai.get_reply([{"role": "user", "content": "ping"}])
        if "problema técnico" in resp.lower():
            return jsonify({"status": "degraded", "error": "DeepSeek fallback response"}), 503
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 503


@app.post("/admin/run-vera")
def run_vera_tests():
    """
    Run Vera stress tests (manual, protected).
    Requires VERA_RUN_TOKEN env var and matching Bearer token.
    """
    import os
    import sys
    import subprocess
    import threading
    import uuid
    import time as _time

    # simple in-memory job store (ephemeral)
    if not hasattr(app, "_vera_jobs"):
        app._vera_jobs = {}
        app._vera_jobs_lock = threading.Lock()

    expected = os.environ.get("VERA_RUN_TOKEN", "")
    auth = request.headers.get("Authorization", "")
    if not expected or auth != f"Bearer {expected}":
        return jsonify({"error": "unauthorized"}), 403

    data = request.get_json(silent=True) or {}
    category = (data.get("category") or "nuevos").strip()
    if category not in ("nuevos", "nuevos2"):
        return jsonify({"error": "invalid category"}), 400

    job_id = uuid.uuid4().hex
    cmd = [sys.executable, "test_vera.py", category]

    def _run_job():
        started = _time.time()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=900,
            )
            out = (result.stdout or "")[-8000:]
            err = (result.stderr or "")[-4000:]
            payload = {
                "status": "ok" if result.returncode == 0 else "error",
                "returncode": result.returncode,
                "stdout_tail": out,
                "stderr_tail": err,
                "elapsed": round(_time.time() - started, 2),
            }
        except subprocess.TimeoutExpired:
            payload = {"status": "error", "error": "timeout", "elapsed": round(_time.time() - started, 2)}

        with app._vera_jobs_lock:
            app._vera_jobs[job_id] = payload

    with app._vera_jobs_lock:
        app._vera_jobs[job_id] = {"status": "running"}

    threading.Thread(target=_run_job, daemon=True).start()
    return jsonify({"status": "accepted", "job_id": job_id}), 202


@app.get("/admin/run-vera/status")
def run_vera_status():
    import os
    expected = os.environ.get("VERA_RUN_TOKEN", "")
    auth = request.headers.get("Authorization", "")
    if not expected or auth != f"Bearer {expected}":
        return jsonify({"error": "unauthorized"}), 403

    job_id = request.args.get("job_id", "")
    if not job_id:
        return jsonify({"error": "missing job_id"}), 400
    if not hasattr(app, "_vera_jobs"):
        return jsonify({"error": "not_found"}), 404
    with app._vera_jobs_lock:
        payload = app._vera_jobs.get(job_id)
    if not payload:
        return jsonify({"error": "not_found"}), 404
    return jsonify(payload), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
