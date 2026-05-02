"""
Main Flask app.
Handles Meta WhatsApp, Facebook Messenger, and Instagram Direct webhook messages.
"""
from __future__ import annotations

import logging
import json
import os
import re
import time
import threading
import hmac
import hashlib
import ipaddress
import socket
import io
from urllib.parse import urlparse
import requests
from functools import wraps
from flask import Flask, request, jsonify, render_template
from config import VERIFY_TOKEN, PAGE_ACCESS_TOKEN, DASHBOARD_PLAN, DASHBOARD_SECRET_KEY, ASSET_VERSION, PHONE_NUMBER_ID
import analytics
import conversations
import ai
import lead_qualifier
import visit_scheduler
import whatsapp
import drive_photos
import sheets

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

# Graceful shutdown: checkpoint SQLite WAL so no data is lost on redeploy
import signal
import atexit
atexit.register(analytics.shutdown_db)
def _handle_sigterm(*_):
    analytics.shutdown_db()
    raise SystemExit(0)
signal.signal(signal.SIGTERM, _handle_sigterm)

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
DEBOUNCE_SECONDS = 8
DEBOUNCE_SECONDS_META = 12  # IG/FB users send rapid short messages
MAX_MESSAGE_LENGTH = 4000
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _health_auth_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        expected = os.environ.get("HEALTH_TOKEN", "") or os.environ.get("DASHBOARD_TOKEN", "")
        if not expected:
            return f(*args, **kwargs)
        token = request.args.get("token", "")
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = token or auth.replace("Bearer ", "", 1).strip()
        if token == expected:
            return f(*args, **kwargs)
        return jsonify({"error": "unauthorized"}), 403
    return wrapper


def _verify_meta_signature(raw_body: bytes) -> bool:
    """Verify X-Hub-Signature-256 for Meta webhooks when META_APP_SECRET is set."""
    secret = os.environ.get("META_APP_SECRET", "")
    if not secret:
        logger.warning("META_APP_SECRET not set — webhook signature not verified")
        return True
    header_sig = request.headers.get("X-Hub-Signature-256", "")
    if not header_sig:
        logger.warning("Missing X-Hub-Signature-256 header")
        return False
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header_sig)


def _is_private_host(host: str) -> bool:
    if not host:
        return True
    host = host.strip().lower()
    if host == "localhost" or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        try:
            ip = ipaddress.ip_address(socket.gethostbyname(host))
        except Exception:
            return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_reserved
        or ip.is_link_local
        or ip.is_multicast
    )


def _is_safe_image_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    if parsed.port and parsed.port not in (80, 443):
        return False
    if _is_private_host(parsed.hostname or ""):
        return False
    return True


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

# In-memory webhook log for debugging (last 20 payloads)
_webhook_log: list = []
_webhook_log_lock = threading.Lock()

@app.post("/webhook")
def receive_message():
    raw_body = request.get_data(cache=False) or b""
    if not _verify_meta_signature(raw_body):
        return "Forbidden", 403
    try:
        data = json.loads(raw_body.decode("utf-8") or "{}")
    except Exception:
        data = {}
    logger.info("Webhook POST received: %s", json.dumps(data)[:500])

    # Store for debugging
    import datetime
    with _webhook_log_lock:
        _webhook_log.append({
            "ts": datetime.datetime.utcnow().isoformat(),
            "payload": json.dumps(data)[:2000],
            "headers": {k: v for k, v in request.headers if k.lower().startswith(("x-hub", "content", "user-agent"))},
        })
        if len(_webhook_log) > 20:
            _webhook_log.pop(0)

    # Always return 200 quickly so Meta doesn't retry
    try:
        _process_payload(data)
    except Exception as e:
        logger.error("Error processing webhook payload: %s", e, exc_info=True)

    return jsonify({"status": "ok"}), 200


@app.get("/health/webhook-log")
@_health_auth_required
def webhook_log():
    """Show last 20 webhook payloads received (debug only)."""
    with _webhook_log_lock:
        return jsonify({"count": len(_webhook_log), "entries": list(_webhook_log)}), 200


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
    msg_id = msg.get("id") or msg.get("message_id")

    if not phone:
        return

    if msg_type == "text":
        text = (msg.get("text") or {}).get("body", "").strip()
        if not text:
            return
        if msg_id and not analytics.mark_message_processed(msg_id, channel="whatsapp"):
            logger.info("Duplicate WhatsApp message ignored: %s", msg_id)
            return
        logger.info("Incoming message from %s: %s", phone, text)
        _enqueue(phone, text)
        return

    if msg_type == "interactive":
        text = _extract_interactive_text(msg)
        if text:
            if msg_id and not analytics.mark_message_processed(msg_id, channel="whatsapp"):
                logger.info("Duplicate WhatsApp message ignored: %s", msg_id)
                return
            logger.info("Incoming interactive message from %s: %s", phone, text)
            _enqueue(phone, text)
            return
        logger.info("Interactive message without text from %s", phone)
        return

    if msg_type == "button":
        text = (msg.get("button") or {}).get("text", "").strip()
        if text:
            if msg_id and not analytics.mark_message_processed(msg_id, channel="whatsapp"):
                logger.info("Duplicate WhatsApp message ignored: %s", msg_id)
                return
            logger.info("Incoming button message from %s: %s", phone, text)
            _enqueue(phone, text)
            return
        logger.info("Button message without text from %s", phone)
        return

    if msg_type == "audio":
        media_id = (msg.get("audio") or {}).get("id")
        if msg_id and not analytics.mark_message_processed(msg_id, channel="whatsapp"):
            logger.info("Duplicate WhatsApp message ignored: %s", msg_id)
            return
        if media_id:
            threading.Thread(
                target=_transcribe_and_enqueue,
                args=(phone, media_id),
                daemon=True,
            ).start()
        else:
            _enqueue(phone, "[audio recibido — no se pudo procesar]")
        return

    if msg_type == "image":
        media_id = (msg.get("image") or {}).get("id")
        caption = (msg.get("image") or {}).get("caption", "").strip()
        if msg_id and not analytics.mark_message_processed(msg_id, channel="whatsapp"):
            logger.info("Duplicate WhatsApp message ignored: %s", msg_id)
            return
        if media_id:
            threading.Thread(
                target=_save_chat_photo,
                args=(phone, media_id, caption),
                daemon=True,
            ).start()
        else:
            _enqueue(phone, caption or "[imagen recibida]")
        return

    if msg_type in ("video", "document"):
        if msg_id and not analytics.mark_message_processed(msg_id, channel="whatsapp"):
            logger.info("Duplicate WhatsApp message ignored: %s", msg_id)
            return
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


def _save_chat_photo(phone: str, media_id: str, caption: str = ""):
    """Download a user-sent photo, save it locally, store for vision, and enqueue."""
    import uuid
    data = _download_whatsapp_media(media_id)
    if not data:
        _enqueue(phone, caption or "[imagen recibida]")
        return
    chat_dir = os.path.join(MEDIA_UPLOAD_DIR, "chat_photos")
    os.makedirs(chat_dir, exist_ok=True)
    filename = f"{uuid.uuid4().hex[:12]}.jpg"
    filepath = os.path.join(chat_dir, filename)
    with open(filepath, "wb") as f:
        f.write(data)
    # Store image for GPT vision processing
    _pending_images[phone] = {"data": data, "mime": "image/jpeg"}
    img_marker = f"[img:/uploads/chat_photos/{filename}]"
    text = f"{caption}\n{img_marker}" if caption else f"[El cliente envió una imagen]\n{img_marker}"
    _enqueue(phone, text)


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
            _pending[phone]["gen"] += 1
        else:
            _pending[phone] = {"texts": [text], "gen": 0}

        gen = _pending[phone]["gen"]
        timer = threading.Timer(DEBOUNCE_SECONDS, _flush, args=[phone, gen])
        _pending[phone]["timer"] = timer
        timer.start()


def _flush(phone: str, gen: int):
    """Called after the debounce window — combine buffered texts and reply once."""
    with _pending_lock:
        if phone not in _pending:
            return
        # Stale timer — a newer message arrived and bumped the generation
        if _pending[phone]["gen"] != gen:
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


def _extract_name(text: str, asked_for_name: bool = False):
    """Detect user's name from Spanish and English self-introduction patterns.
    asked_for_name: only match bare single-word responses when Vera already asked."""
    _LETTER = r"[a-záéíóúüñA-ZÁÉÍÓÚÜÑ]"
    _NOT_NAMES = {"buenas", "buen", "bueno", "buenos", "hola", "bien", "todo", "algo",
                  "como", "esta", "este", "esto", "eso", "esa", "que", "una", "uno",
                  "con", "por", "para", "muy", "mas", "les", "los", "las", "del",
                  "dia", "tarde", "noche", "aca", "ahi", "alla",
                  # Common real estate / inquiry words (avoid false name matches)
                  "precio", "precios", "alquiler", "alquilar", "compra", "comprar",
                  "venta", "vender", "depto", "departamento", "departamentos",
                  "casa", "casas", "info", "información", "informacion", "consulta",
                  "presupuesto", "fotos", "foto", "horarios", "horario",
                  "disponible", "disponibles", "ubicación", "ubicacion",
                  "dirección", "direccion", "metros", "cochera", "duplex",
                  "monoambiente", "terreno", "lote", "local", "oficina",
                  "propiedad", "propiedades", "ambientes", "dormitorios",
                  "habitaciones", "expensas", "garantía", "garantia",
                  "contrato", "requisitos", "condiciones", "valores",
                  "mañana", "manana", "hoy", "ahora", "listo", "dale", "ok",
                  "gracias", "chau", "perdon", "disculpa", "claro", "perfecto", "si",
                  "genial", "excelente", "interesado", "interesada", "averiguar",
                  "consultar", "reserva", "reservar", "visita", "visitar",
                  # English stop words
                  "hey", "hello", "hi", "good", "fine", "great", "well", "thanks",
                  "thank", "yes", "yeah", "yep", "sure", "okay", "the", "and",
                  "not", "but", "just", "here", "there", "looking", "interested",
                  "want", "need", "have", "are", "was", "were", "been", "being",
                  "price", "rent", "buy", "sale", "available", "photos", "info"}
    patterns = [
        # Spanish patterns
        r"(?:soy|me llamo|mi nombre es|mi nombre:)\s+(" + _LETTER + r"{2,20})",
        r"(?:habla|te escribe|les escribe|te contacta|de parte de)\s+(" + _LETTER + r"{2,20})",
        r"(?:les\s+habla|acá\s+habla|aca\s+habla)\s+(" + _LETTER + r"{2,20})",
        r"^con\s+(" + _LETTER + r"{2,20})\s*[,.]?\s*$",
        r"^con\s+(" + _LETTER + r"{2,20})[,]\s",
        # English patterns
        r"(?:i'?\s*m|i\s+am|my\s+name\s+is|my\s+name'?\s*s|this\s+is|it'?\s*s)\s+(" + _LETTER + r"{2,20})",
        r"(?:they\s+call\s+me|call\s+me|you\s+can\s+call\s+me)\s+(" + _LETTER + r"{2,20})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            name = m.group(1).lower()
            if name not in _NOT_NAMES:
                return m.group(1).capitalize()
    # Bare name response: single word (2-20 letters), possibly with punctuation
    # Matches responses like "Cande", "cande!", "Cande."
    # Only applies when Vera already asked "con quién hablo?" to avoid
    # treating inquiry words like "Precio" as names.
    if asked_for_name:
        bare = re.match(r"^\s*(" + _LETTER + r"{2,20})\s*[!.,;:?]*\s*$", text)
        if bare:
            name = bare.group(1).lower()
            if name not in _NOT_NAMES:
                return bare.group(1).capitalize()
    return None


def _process_reply(identifier: str, user_text: str, channel: str, send_fn,
                    send_image_fn=None):
    """Shared AI pipeline for all channels (WhatsApp, Facebook, Instagram)."""
    is_new = len(conversations.get_messages(identifier)) == 0
    analytics.log_event("message_in", identifier, channel=channel)
    conversations.add_message(identifier, "user", user_text, channel=channel)

    # Always extract lead info from every message, even during agent takeover
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

    # Check if Vera already asked for the name (enables bare single-word detection)
    history_so_far = conversations.get_messages(identifier)
    _asked_name = any(
        m["role"] == "assistant" and re.search(
            r"(?:qui[eé]n hablo|tu nombre|me dec[ií]s tu nombre|c[oó]mo te llam[aá]s|who am i speaking|what.?s your name)",
            m["content"], re.IGNORECASE
        )
        for m in history_so_far
    )
    name = _extract_name(user_text, asked_for_name=_asked_name)
    if name:
        current = conversations.get_lead(identifier)
        if not current.get("name"):
            conversations.update_lead(identifier, name=name)
            logger.info("Name extracted for %s: %s", identifier, name)

    # If human agent has taken over, don't auto-reply but lead info is already extracted above
    if conversations.is_agent_takeover(identifier):
        logger.info("AI paused for %s (agent takeover) — message stored, no auto-reply", identifier)
        return

    if is_new:
        analytics.log_event("new_conversation", identifier, channel=channel,
                             operation=operation, property_type=prop_type)

    history = conversations.get_messages(identifier)
    lead = conversations.get_lead(identifier)
    # Check for pending image to include in vision request
    image_data = _pending_images.pop(identifier, None)
    ai_response = ai.get_reply(history, lead=lead, image=image_data)

    clean_response = lead_qualifier.process(identifier, ai_response, channel=channel)
    clean_response = visit_scheduler.process(identifier, clean_response)
    clean_response = clean_response.replace("¿", "").replace("¡", "")
    # Strip markdown-style separators (---) the model sometimes adds
    clean_response = re.sub(r'\n*-{3,}\n*', '\n\n', clean_response).strip()

    # Safety net: strip re-introduction if conversation is already in progress
    history_after = conversations.get_messages(identifier)
    if len(history_after) > 2:
        # Broad pattern to catch any variant of "Hola soy Vera / con quién hablo"
        clean_response = re.sub(
            r'[Hh]ola[!.]?\s*[Ss]oy Vera[^?!.]*[?.!]*\s*',
            '',
            clean_response,
        ).strip()
        # Also catch standalone "con quién hablo?" variants
        clean_response = re.sub(
            r'[Cc]on\s+qui[eé]n\s+hablo[?.!]*\s*',
            '',
            clean_response,
        ).strip()
        # If stripping left nothing useful, don't send empty
        if not clean_response:
            logger.warning("Safety net stripped entire response for %s — skipping send", identifier)
            return

    # Detect Drive URLs — download photos and send as images
    drive_urls = drive_photos.extract_drive_urls(clean_response)
    import uuid as _uuid

    # Track property inquiries when photos are sent
    if drive_urls:
        _url_to_property = {}
        for listing in sheets.get_listings():
            furl = str(listing.get("fotos_url", "") or "").strip()
            if furl:
                _url_to_property[furl] = listing.get("titulo", "")
        for url_info in drive_urls:
            matched_title = _url_to_property.get(url_info["url"], "")
            if matched_title:
                analytics.log_event("property_inquiry", identifier, channel=channel,
                                    property=matched_title)

    if drive_urls and send_image_fn:
        # Split response text around Drive URLs to interleave text + photos per property
        # e.g. "Acá las del PH: <url1>\n\nY las del dúplex: <url2>\n\nFijate."
        #   → ["Acá las del PH:", "Y las del dúplex:", "Fijate."]
        text_parts = clean_response
        for u in drive_urls:
            text_parts = text_parts.replace(u["url"], "\x00")
        segments = [s.strip() for s in text_parts.split("\x00")]
        # Clean decoration-only segments (e.g. bare "---" separators)
        segments = [re.sub(r'^-{2,}\s*$', '', s, flags=re.MULTILINE).strip() for s in segments]
        # segments has len(drive_urls)+1 parts: before_url1, between, ..., after_last

        all_sent_markers = []
        all_failed = 0
        all_failed_urls = []

        for i, url_info in enumerate(drive_urls):
            # Send the text segment before this URL's photos
            text_before = segments[i] if i < len(segments) else ""
            text_before = re.sub(r'\n\s*\n\s*\n', '\n\n', text_before).strip()
            if text_before:
                send_fn(identifier, text_before)
                conversations.add_message(identifier, "assistant", text_before, channel=channel)

            # Download and send this URL's photos
            photos = drive_photos.download_photos([url_info])
            if photos:
                for _name, img_data, mime in photos:
                    try:
                        success = send_image_fn(identifier, img_data, mime)
                        if success:
                            chat_dir = os.path.join(MEDIA_UPLOAD_DIR, "chat_photos")
                            os.makedirs(chat_dir, exist_ok=True)
                            ext = mime.split("/")[-1].replace("jpeg", "jpg") if mime else "jpg"
                            fname = f"{_uuid.uuid4().hex[:12]}.{ext}"
                            with open(os.path.join(chat_dir, fname), "wb") as fout:
                                fout.write(img_data)
                            all_sent_markers.append(f"[img:/uploads/chat_photos/{fname}]")
                        else:
                            all_failed += 1
                            all_failed_urls.append(url_info["url"])
                        time.sleep(1)
                    except Exception as e:
                        logger.error("Failed sending photo to %s: %s", identifier, e)
                        all_failed += 1
                        all_failed_urls.append(url_info["url"])
            else:
                # Drive download failed — send URL as text fallback
                send_fn(identifier, url_info["url"])
                conversations.add_message(identifier, "assistant", url_info["url"], channel=channel)
                logger.warning("Drive download failed for %s — sent URL as text", url_info["url"])

        # Send trailing text after the last URL (e.g. "Fijate y decime")
        trailing = segments[-1] if len(segments) > len(drive_urls) else ""
        trailing = re.sub(r'\n\s*\n\s*\n', '\n\n', trailing).strip()
        if trailing:
            send_fn(identifier, trailing)
            conversations.add_message(identifier, "assistant", trailing, channel=channel)

        # Fallback: if any image sends failed, re-send those Drive URLs
        if all_failed > 0 and all_failed_urls:
            unique_urls = list(dict.fromkeys(all_failed_urls))
            fallback_text = "Te paso los links de las fotos: " + " ".join(unique_urls)
            send_fn(identifier, fallback_text)
            conversations.add_message(identifier, "assistant", fallback_text, channel=channel)
            logger.warning("Sent %d Drive URL(s) as text fallback after %d image send failures", len(unique_urls), all_failed)

        if all_sent_markers:
            conversations.add_message(identifier, "assistant", "\n".join(all_sent_markers), channel=channel)

    else:
        # No Drive URLs — check for external image URLs (e.g. esquelprop.com)
        ext_photos: list[tuple] = []
        if send_image_fn:
            _IMG_URL_RE = re.compile(r'(https?://[^\s)>\]]+\.(?:jpg|jpeg|png|webp)(?:\?[^\s)>\]]*)?)', re.IGNORECASE)
            img_urls = _IMG_URL_RE.findall(clean_response)
            for img_url in img_urls[:5]:
                try:
                    if not _is_safe_image_url(img_url):
                        logger.warning("Blocked unsafe image URL: %s", img_url)
                        continue
                    r = requests.get(img_url, timeout=10, stream=True, allow_redirects=False)
                    if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
                        content_len = r.headers.get("content-length")
                        if content_len and int(content_len) > MAX_IMAGE_BYTES:
                            logger.warning("Image too large (%s bytes): %s", content_len, img_url)
                            continue
                        buf = io.BytesIO()
                        for chunk in r.iter_content(chunk_size=8192):
                            if not chunk:
                                continue
                            buf.write(chunk)
                            if buf.tell() > MAX_IMAGE_BYTES:
                                logger.warning("Image exceeded max size while downloading: %s", img_url)
                                buf = None
                                break
                        if not buf:
                            continue
                        mime = r.headers.get("content-type", "image/jpeg").split(";")[0]
                        ext_photos.append((img_url.split("/")[-1], buf.getvalue(), mime))
                        clean_response = clean_response.replace(img_url, "").strip()
                except Exception as e:
                    logger.warning("Failed to download external image %s: %s", img_url, e)
            clean_response = re.sub(r'\n\s*\n\s*\n', '\n\n', clean_response).strip()

        if clean_response:
            conversations.add_message(identifier, "assistant", clean_response, channel=channel)
            send_fn(identifier, clean_response)

        # Send external photos
        sent_markers = []
        for _name, img_data, mime in ext_photos:
            try:
                success = send_image_fn(identifier, img_data, mime)
                if success:
                    chat_dir = os.path.join(MEDIA_UPLOAD_DIR, "chat_photos")
                    os.makedirs(chat_dir, exist_ok=True)
                    ext = mime.split("/")[-1].replace("jpeg", "jpg") if mime else "jpg"
                    fname = f"{_uuid.uuid4().hex[:12]}.{ext}"
                    with open(os.path.join(chat_dir, fname), "wb") as fout:
                        fout.write(img_data)
                    sent_markers.append(f"[img:/uploads/chat_photos/{fname}]")
                time.sleep(1)
            except Exception as e:
                logger.error("Failed sending photo to %s: %s", identifier, e)
        if sent_markers:
            conversations.add_message(identifier, "assistant", "\n".join(sent_markers), channel=channel)


def _reply(phone: str, user_text: str):
    _process_reply(phone, user_text, "whatsapp", whatsapp.send_message,
                   send_image_fn=whatsapp.send_image)


# ---------------------------------------------------------------------------
# Facebook Messenger / Instagram Direct support
# To enable: subscribe the webhook in Meta App Dashboard under Messenger and
# Instagram settings (subscribed_fields: messages, messaging_postbacks).
# ---------------------------------------------------------------------------

# Track recent bot-sent messages to distinguish bot echoes from human agent echoes.
# Key: recipient_id, Value: timestamp of last bot-sent message.
_bot_sent_ts: dict[str, float] = {}
_BOT_ECHO_WINDOW = 60  # seconds — echoes within this window after bot send are ignored

# Temporary storage for user-sent images pending AI processing.
# Key: phone/sender_id, Value: {"data": bytes, "mime": str}
_pending_images: dict = {}

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
            return False
        import time as _time
        _bot_sent_ts[recipient_id] = _time.time()
        return True
    except Exception as e:
        logger.error("Failed to send Meta message: %s", e)
        return False


def _send_meta_image(recipient_id: str, image_data: bytes, mime_type: str = "image/jpeg"):
    """Send an image via Meta Graph API (Facebook Messenger / Instagram Direct).
    Retries once on failure. Returns True on success."""
    if not PAGE_ACCESS_TOKEN:
        logger.warning("PAGE_ACCESS_TOKEN not set — cannot send Meta image.")
        return False
    ext = mime_type.split("/")[-1].replace("jpeg", "jpg")
    for attempt in range(2):
        try:
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
                timeout=45,
            )
            if resp.ok:
                import time as _time
                _bot_sent_ts[recipient_id] = _time.time()
                return True
            logger.error("Meta image API error (attempt %d) %s: %s", attempt + 1, resp.status_code, resp.text)
        except Exception as e:
            logger.error("Failed to send Meta image (attempt %d): %s", attempt + 1, e)
        if attempt == 0:
            time.sleep(1)
    return False


def _get_meta_profile_name(sender_id: str, channel: str = "facebook") -> str | None:
    """Fetch the user's name from Meta Graph API (FB & IG).
    Instagram only supports 'name' and 'username'; Facebook supports 'first_name' too."""
    if not PAGE_ACCESS_TOKEN:
        logger.warning("No PAGE_ACCESS_TOKEN — cannot fetch profile name for %s", sender_id)
        return None
    # Instagram doesn't support first_name field
    fields = "name,username" if channel == "instagram" else "first_name,name,username"
    try:
        resp = requests.get(
            f"https://graph.facebook.com/v21.0/{sender_id}",
            params={"fields": fields, "access_token": PAGE_ACCESS_TOKEN},
            timeout=5,
        )
        if resp.ok:
            data = resp.json()
            logger.info("Meta profile data for %s: %s", sender_id,
                        {k: v for k, v in data.items() if k != "id"})
            # Prefer first_name (FB only), fall back to first word of full name, then username
            if data.get("first_name"):
                return data["first_name"]
            if data.get("name"):
                return data["name"].split()[0]
            if data.get("username"):
                return data["username"]
            logger.warning("Meta profile for %s returned no name fields", sender_id)
        else:
            logger.warning("Meta profile lookup failed for %s: HTTP %s — %s",
                           sender_id, resp.status_code, resp.text[:200])
    except Exception as e:
        logger.warning("Could not fetch Meta profile for %s: %s", sender_id, e)
    return None


def _download_meta_image(sender_id: str, img_url: str):
    """Download image from Meta CDN and store for vision processing."""
    try:
        # Some IG CDN URLs require the page access token
        headers = {}
        if PAGE_ACCESS_TOKEN:
            headers["Authorization"] = f"Bearer {PAGE_ACCESS_TOKEN}"
        resp = requests.get(img_url, headers=headers, timeout=15)
        if resp.status_code == 404 and PAGE_ACCESS_TOKEN:
            # Retry without auth header (some FB CDN URLs don't need it)
            resp = requests.get(img_url, timeout=15)
        resp.raise_for_status()
        _pending_images[sender_id] = {"data": resp.content, "mime": resp.headers.get("content-type", "image/jpeg")}
        logger.info("Image downloaded for vision: %s (%d bytes)", sender_id, len(resp.content))
    except Exception as e:
        logger.error("Failed to download Meta image for %s: %s", sender_id, e)


def _reply_meta(sender_id: str, user_text: str, channel: str):
    """Run the AI pipeline for a Facebook/Instagram message and reply."""
    if DASHBOARD_PLAN == "starter":
        logger.info("Meta message ignored — Starter plan does not include FB/IG channels.")
        return

    # Mark as meta channel so AI never asks for name on FB/IG
    current = conversations.get_lead(sender_id)
    if not current.get("channel"):
        conversations.update_lead(sender_id, channel=channel)
    if not current.get("name"):
        profile_name = _get_meta_profile_name(sender_id, channel=channel)
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
    raw_body = request.get_data(cache=False) or b""
    if not _verify_meta_signature(raw_body):
        return "Forbidden", 403
    try:
        data = json.loads(raw_body.decode("utf-8") or "{}")
    except Exception:
        data = {}
    # object is "page" for Facebook Messenger, "instagram" for Instagram Direct
    obj_type = data.get("object", "")
    if obj_type not in ("page", "instagram"):
        return jsonify({"status": "ok"}), 200
    try:
        for entry in data.get("entry", []):
            for messaging in entry.get("messaging", []):
                sender_id = messaging.get("sender", {}).get("id")
                message = messaging.get("message", {})
                # Echo = message sent FROM the page. app_id present = sent by bot API, skip.
                # No app_id = sent by human from page inbox → pause Vera.
                # BUT: Instagram echoes sometimes lack app_id even for bot messages,
                # so also check if bot sent a message to this recipient recently.
                if message.get("is_echo"):
                    if message.get("app_id"):
                        # Bot's own message echoed back — ignore
                        continue
                    recipient_id = messaging.get("recipient", {}).get("id")
                    if recipient_id:
                        import time as _time
                        last_bot_send = _bot_sent_ts.get(recipient_id, 0)
                        if (_time.time() - last_bot_send) < _BOT_ECHO_WINDOW:
                            logger.info("Ignoring echo for %s — bot sent message %ds ago",
                                        recipient_id, int(_time.time() - last_bot_send))
                            continue
                        logger.info("Human agent replied to %s via %s — pausing AI", recipient_id, obj_type)
                        conversations.set_agent_takeover(recipient_id)
                    continue
                # Handle image attachments (IG/FB)
                attachments = message.get("attachments", {}).get("data", []) if isinstance(message.get("attachments"), dict) else message.get("attachments", [])
                img_att = next((a for a in attachments if a.get("type") == "image"), None) if attachments else None
                if img_att and sender_id:
                    img_url = img_att.get("payload", {}).get("url", "")
                    if img_url:
                        _download_meta_image(sender_id, img_url)
                if not message.get("text") and img_att:
                    mid_img = message.get("mid", "")
                    if mid_img:
                        ch = "facebook" if obj_type == "page" else "instagram"
                        if not analytics.mark_message_processed(mid_img, channel=ch):
                            continue
                    channel = "facebook" if obj_type == "page" else "instagram"
                    key = (channel, sender_id)
                    # If text is already queued in the debouncer, skip placeholder —
                    # the image is already stored in _pending_images and will be
                    # attached when the debounced text flushes.
                    with _pending_meta_lock:
                        already_queued = key in _pending_meta
                    if already_queued:
                        logger.info("Meta (%s) image from %s — text already queued, skipping placeholder", obj_type, sender_id)
                    else:
                        logger.info("Meta (%s) image-only from %s — enqueuing placeholder", obj_type, sender_id)
                        _enqueue_meta(sender_id, "[El cliente envió una imagen]", channel)
                    continue
                if not message.get("text"):
                    continue
                # Deduplicate by message ID (Meta sometimes sends the same webhook twice)
                mid = message.get("mid", "")
                if mid:
                    channel = "facebook" if obj_type == "page" else "instagram"
                    if not analytics.mark_message_processed(mid, channel=channel):
                        logger.info("Duplicate Meta message ignored: %s", mid)
                        continue
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

def _enqueue_meta(sender_id: str, text: str, channel: str):
    text = text[:MAX_MESSAGE_LENGTH]
    key = (channel, sender_id)
    with _pending_meta_lock:
        if key in _pending_meta:
            _pending_meta[key]["timer"].cancel()
            _pending_meta[key]["texts"].append(text)
            _pending_meta[key]["gen"] += 1
        else:
            _pending_meta[key] = {"texts": [text], "channel": channel, "sender_id": sender_id, "gen": 0}
        gen = _pending_meta[key]["gen"]
        timer = threading.Timer(DEBOUNCE_SECONDS_META, _flush_meta, args=[key, gen])
        _pending_meta[key]["timer"] = timer
        timer.start()


def _flush_meta(key, gen: int):
    with _pending_meta_lock:
        if key not in _pending_meta:
            return
        if _pending_meta[key]["gen"] != gen:
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
    # Informational (not part of health check)
    checks["db_backend"] = "postgresql" if analytics._USE_PG else "sqlite"
    if not analytics._USE_PG:
        is_mount = os.path.ismount("/data")
        is_dir = os.path.isdir("/data")
        if is_mount:
            checks["volume"] = "mounted (persistent)"
        elif is_dir:
            checks["volume"] = "⚠ /data exists but NOT a mount — ephemeral, data WILL be lost!"
        else:
            checks["volume"] = "NOT MOUNTED — data lost on redeploy"
        checks["db_path"] = analytics._DB_PATH
    checks["db_stats"] = analytics.db_stats()
    return jsonify({"status": "ok" if all_ok else "degraded", "checks": checks}), 200 if all_ok else 503


@app.get("/health/whatsapp")
@_health_auth_required
def health_whatsapp():
    """Diagnose WhatsApp Cloud API phone number status and webhook config."""
    token = os.environ.get("WHATSAPP_TOKEN", "")
    phone_id = PHONE_NUMBER_ID
    result = {}
    hdrs = {"Authorization": f"Bearer {token}"}

    # 1. Phone number info (including webhook_configuration)
    try:
        r = requests.get(
            f"https://graph.facebook.com/v21.0/{phone_id}",
            params={"fields": "display_phone_number,verified_name,quality_rating,platform_type,status,name_status,messaging_limit_tier,is_official_business_account,account_mode,webhook_configuration"},
            headers=hdrs, timeout=10,
        )
        result["phone"] = r.json()
    except Exception as e:
        result["phone"] = {"error": str(e)}

    # 2. Debug token
    try:
        r = requests.get(
            f"https://graph.facebook.com/v21.0/debug_token",
            params={"input_token": token},
            headers=hdrs, timeout=10,
        )
        result["token_debug"] = r.json()
    except Exception as e:
        result["token_debug"] = {"error": str(e)}

    app_id = result.get("token_debug", {}).get("data", {}).get("app_id")
    waba_id = "1921482308505030"

    # 3. WABA info
    try:
        r = requests.get(f"https://graph.facebook.com/v21.0/{waba_id}",
                         params={"fields": "name,currency,timezone_id,message_template_namespace,account_review_status,on_behalf_of_business_info,primary_funding_id,purchase_order_number,id"},
                         headers=hdrs, timeout=10)
        result["waba_info"] = r.json()
    except Exception as e:
        result["waba_info"] = {"error": str(e)}

    # 4. WABA phone numbers (confirm this phone belongs to this WABA)
    try:
        r = requests.get(f"https://graph.facebook.com/v21.0/{waba_id}/phone_numbers",
                         params={"fields": "display_phone_number,verified_name,status,quality_rating,platform_type,id"},
                         headers=hdrs, timeout=10)
        result["waba_phones"] = r.json()
    except Exception as e:
        result["waba_phones"] = {"error": str(e)}

    # 5. WABA subscribed apps
    try:
        r = requests.get(f"https://graph.facebook.com/v21.0/{waba_id}/subscribed_apps", headers=hdrs, timeout=10)
        result["waba_subs"] = r.json()
    except Exception as e:
        result["waba_subs"] = {"error": str(e)}

    # 6. App subscriptions (needs app secret — will likely fail but try)
    try:
        r = requests.get(f"https://graph.facebook.com/v21.0/{app_id}/subscriptions", headers=hdrs, timeout=10)
        result["app_subscriptions"] = r.json()
    except Exception as e:
        result["app_subscriptions"] = {"error": str(e)}

    # 7. Phone registration status (read-only, no side effects)
    result["phone_register"] = "Use POST /health/waba-subscribe?register=1 to re-register if needed"

    return jsonify(result), 200


@app.get("/health/waba-subscribe")
@_health_auth_required
def waba_subscribe():
    """Subscribe app to WABA for webhook delivery."""
    token = os.environ.get("WHATSAPP_TOKEN", "")
    waba_id = request.args.get("waba_id", "1921482308505030")
    hdrs = {"Authorization": f"Bearer {token}"}
    result = {}
    # Check current subscriptions
    try:
        r = requests.get(f"https://graph.facebook.com/v21.0/{waba_id}/subscribed_apps", headers=hdrs, timeout=10)
        result["current_subs"] = r.json() if r.ok else {"error": f"HTTP {r.status_code}"}
    except Exception as e:
        result["current_subs"] = {"error": str(e)}
    # Subscribe
    try:
        r2 = requests.post(f"https://graph.facebook.com/v21.0/{waba_id}/subscribed_apps", headers=hdrs, timeout=10)
        result["subscribe_result"] = r2.json() if r2.ok else {"error": f"HTTP {r2.status_code}"}
    except Exception as e:
        result["subscribe_result"] = {"error": str(e)}
    # Verify
    try:
        r3 = requests.get(f"https://graph.facebook.com/v21.0/{waba_id}/subscribed_apps", headers=hdrs, timeout=10)
        result["after_subscribe"] = r3.json() if r3.ok else {"error": f"HTTP {r3.status_code}"}
    except Exception as e:
        result["after_subscribe"] = {"error": str(e)}
    return jsonify(result), 200


@app.get("/health/deepseek")
@_health_auth_required
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


@app.get("/health/volume-test")
@_health_auth_required
def volume_test():
    """Test if files persist on the volume across deploys."""
    marker = "/data/.persistence_marker"
    result = {}
    # Read existing marker
    if os.path.exists(marker):
        with open(marker, "r") as f:
            result["previous_marker"] = f.read().strip()
        result["persisted"] = True
    else:
        result["previous_marker"] = None
        result["persisted"] = False
    # Write new marker with current timestamp
    import datetime
    now = datetime.datetime.utcnow().isoformat()
    with open(marker, "w") as f:
        f.write(now)
    result["current_marker"] = now
    # List /data directory
    try:
        result["data_files"] = os.listdir("/data")
    except Exception as e:
        result["data_files"] = str(e)
    return jsonify(result), 200


@app.get("/health/db-verify")
@_health_auth_required
def db_verify():
    """Verify database writes are actually reaching disk."""
    import sqlite3
    db_path = analytics._DB_PATH
    result = {}
    # 1. File info
    try:
        result["file_size"] = os.path.getsize(db_path)
        result["file_exists"] = True
    except Exception as e:
        result["file_exists"] = False
        result["file_error"] = str(e)
    # 2. Check for WAL/SHM files
    result["wal_exists"] = os.path.exists(db_path + "-wal")
    result["shm_exists"] = os.path.exists(db_path + "-shm")
    if result["wal_exists"]:
        result["wal_size"] = os.path.getsize(db_path + "-wal")
    # 3. Read from CACHED connection
    try:
        with analytics._db_lock:
            conn = analytics._get_conn()
            result["cached_journal_mode"] = conn.execute("PRAGMA journal_mode").fetchone()[0]
            result["cached_synchronous"] = conn.execute("PRAGMA synchronous").fetchone()[0]
            result["cached_chat_count"] = conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]
    except Exception as e:
        result["cached_error"] = str(e)
    # 4. Read from FRESH connection (to verify disk state)
    try:
        fresh = sqlite3.connect(db_path)
        result["fresh_chat_count"] = fresh.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]
        result["fresh_journal_mode"] = fresh.execute("PRAGMA journal_mode").fetchone()[0]
        fresh.close()
    except Exception as e:
        result["fresh_error"] = str(e)
    return jsonify(result), 200


@app.get("/health/startup-diag")
@_health_auth_required
def startup_diag():
    """Show init_db() step-by-step diagnostic from this deploy."""
    return jsonify(analytics._startup_diag), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
