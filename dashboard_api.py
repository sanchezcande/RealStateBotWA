"""
Dashboard JSON API Blueprint.
All endpoints require token auth (query param or session).
"""
from __future__ import annotations

import os
from functools import wraps

from flask import Blueprint, request, jsonify, session

import analytics
from config import DASHBOARD_PLAN, GOOGLE_AI_API_KEY

api = Blueprint("dashboard_api", __name__, url_prefix="/api/dashboard")

def _get_google_ai_key() -> str:
    # Read fresh from env to avoid stale import-time values
    return os.environ.get("GOOGLE_AI_API_KEY", "") or GOOGLE_AI_API_KEY


def _require_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        # Check session login
        if session.get("dashboard_auth"):
            return f(*args, **kwargs)
        # Check token param
        token = request.args.get("token", "")
        expected = os.environ.get("DASHBOARD_TOKEN", "")
        if expected and token == expected:
            return f(*args, **kwargs)
        return jsonify({"error": "Acceso denegado"}), 403
    return wrapper


@api.route("/kpis")
@_require_auth
def api_kpis():
    days = request.args.get("days", 30, type=int)
    if days not in (7, 30, 90):
        days = 30
    data = analytics.get_dashboard_data(days=days)
    return jsonify(data)


@api.route("/conversations")
@_require_auth
def api_conversations():
    if DASHBOARD_PLAN == "starter":
        return jsonify({"error": "No disponible en plan Starter"}), 403
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    search = request.args.get("q", "")
    channel = request.args.get("channel", "")
    status = request.args.get("status", "")
    data = analytics.get_conversations_list(
        page=page, per_page=per_page, search=search,
        channel=channel, status=status,
    )
    return jsonify(data)


@api.route("/conversations/<phone_hash>")
@_require_auth
def api_conversation_thread(phone_hash):
    if DASHBOARD_PLAN == "starter":
        return jsonify({"error": "No disponible en plan Starter"}), 403
    data = analytics.get_conversation_thread(phone_hash)
    return jsonify(data)


@api.route("/conversations/<phone_hash>/delete", methods=["POST"])
@_require_auth
def api_delete_conversation(phone_hash):
    """Delete a conversation and all associated data."""
    try:
        phone = analytics.resolve_phone_by_hash(phone_hash)
        with analytics._db_lock:
            conn = analytics._get_conn()
            conn.execute("DELETE FROM chat_messages WHERE phone_hash = ?", (phone_hash,))
            conn.execute("DELETE FROM conversations WHERE phone_hash = ?", (phone_hash,))
            conn.execute("DELETE FROM leads WHERE phone_hash = ?", (phone_hash,))
            conn.execute("DELETE FROM events WHERE phone_hash = ?", (phone_hash,))
            conn.execute("DELETE FROM visits WHERE phone_hash = ?", (phone_hash,))
        # Clear in-memory cache
        if phone:
            import conversations
            with conversations._lock:
                conversations._store.pop(phone, None)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api.route("/conversations/<phone_hash>/reply", methods=["POST"])
@_require_auth
def api_send_reply(phone_hash):
    import conversations
    import whatsapp

    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    pause_bot = data.get("pause_bot", True)

    if not message:
        return jsonify({"error": "Mensaje vacio"}), 400
    if len(message) > 4000:
        return jsonify({"error": "Mensaje demasiado largo (max 4000 caracteres)"}), 400

    phone = analytics.resolve_phone_by_hash(phone_hash)
    if not phone:
        return jsonify({"error": "Conversacion no encontrada"}), 404

    # Detect channel from last message
    try:
        with analytics._db_lock:
            conn = analytics._get_conn()
            row = conn.execute(
                "SELECT channel FROM chat_messages WHERE phone_hash = ? ORDER BY id DESC LIMIT 1",
                (phone_hash,),
            ).fetchone()
        channel = row[0] if row else "whatsapp"
    except Exception:
        channel = "whatsapp"

    # Send via WhatsApp (or Meta for FB/IG)
    if channel in ("instagram", "facebook"):
        from app import _send_meta_message
        _send_meta_message(phone, message)
        success = True
    else:
        success = whatsapp.send_message(phone, message)

    if not success:
        return jsonify({"error": "Error enviando mensaje via WhatsApp"}), 502

    # Record message with role "agent"
    conversations.add_message(phone, "agent", message, channel=channel)

    # Pause the AI bot for this conversation
    if pause_bot:
        conversations.set_agent_takeover(phone)

    return jsonify({"ok": True, "paused": pause_bot})


@api.route("/conversations/<phone_hash>/takeover", methods=["GET", "POST"])
@_require_auth
def api_takeover(phone_hash):
    import conversations

    phone = analytics.resolve_phone_by_hash(phone_hash)
    if not phone:
        return jsonify({"paused": False})

    if request.method == "GET":
        return jsonify({"paused": conversations.is_agent_takeover(phone)})

    data = request.get_json(silent=True) or {}
    action = data.get("action", "pause")

    if action == "resume":
        conversations.clear_agent_takeover(phone)
        return jsonify({"ok": True, "paused": False})
    else:
        conversations.set_agent_takeover(phone)
        return jsonify({"ok": True, "paused": True})


@api.route("/conversations/<phone_hash>/export")
@_require_auth
def api_conversation_export(phone_hash):
    """Export a conversation transcript as a printable HTML page."""
    data = analytics.get_conversation_thread(phone_hash)
    if not data["messages"]:
        return "Conversación no encontrada.", 404

    lead = data.get("lead") or {}
    name = lead.get("name") or "Contacto"

    # Build a minimal standalone HTML page
    msgs_html = []
    for m in data["messages"]:
        role_label = "Cliente" if m["role"] == "user" else ("Agente" if m["role"] == "agent" else "Vera (Bot)")
        role_class = m["role"]
        time_str = m.get("time", "")
        content = m["content"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
        msgs_html.append(f'<div class="msg {role_class}"><div class="role">{role_label} <span class="time">{time_str}</span></div><div class="text">{content}</div></div>')

    lead_info = ""
    if lead:
        parts = []
        if lead.get("operation"): parts.append(f"Operación: {lead['operation']}")
        if lead.get("property_type"): parts.append(f"Tipo: {lead['property_type']}")
        if lead.get("budget"): parts.append(f"Presupuesto: {lead['budget']}")
        if lead.get("timeline"): parts.append(f"Timeline: {lead['timeline']}")
        if parts:
            lead_info = '<div class="lead-info">' + " | ".join(parts) + '</div>'

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Transcripción — {name}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 700px; margin: 0 auto; padding: 20px; color: #1f2937; }}
h1 {{ font-size: 1.3rem; margin-bottom: 4px; }}
.subtitle {{ color: #6b7280; font-size: 0.85rem; margin-bottom: 16px; }}
.lead-info {{ background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px; padding: 10px 14px; margin-bottom: 16px; font-size: 0.85rem; color: #374151; }}
.msg {{ margin-bottom: 12px; padding: 10px 14px; border-radius: 10px; }}
.msg.user {{ background: #dbeafe; margin-left: 40px; }}
.msg.assistant {{ background: #f3f4f6; margin-right: 40px; }}
.msg.agent {{ background: #fef3c7; margin-right: 40px; }}
.role {{ font-size: 0.75rem; font-weight: 600; color: #6b7280; margin-bottom: 4px; }}
.time {{ font-weight: 400; }}
.text {{ font-size: 0.9rem; line-height: 1.5; }}
@media print {{ body {{ padding: 0; }} }}
</style></head><body>
<h1>Transcripción — {name}</h1>
<div class="subtitle">{len(data['messages'])} mensajes</div>
{lead_info}
{''.join(msgs_html)}
<script>window.onload = function() {{ window.print(); }}</script>
</body></html>"""
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


@api.route("/leads")
@_require_auth
def api_leads():
    if DASHBOARD_PLAN == "starter":
        return jsonify({"error": "No disponible en plan Starter"}), 403
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    operation = request.args.get("operation", "")
    sort = request.args.get("sort", "updated_at")
    data = analytics.get_leads_list(page=page, per_page=per_page, operation=operation, sort=sort)
    return jsonify(data)


@api.route("/visits")
@_require_auth
def api_visits():
    if DASHBOARD_PLAN == "starter":
        return jsonify({"error": "No disponible en plan Starter"}), 403
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")
    status = request.args.get("status", "")
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    data = analytics.get_visits_list(
        date_from=date_from, date_to=date_to, status=status,
        page=page, per_page=per_page,
    )
    return jsonify(data)


@api.route("/visits/calendar")
@_require_auth
def api_visits_calendar():
    if DASHBOARD_PLAN == "starter":
        return jsonify({"error": "No disponible en plan Starter"}), 403
    month = request.args.get("month", "")
    if not month:
        from datetime import datetime
        month = datetime.now().strftime("%Y-%m")
    data = analytics.get_visits_calendar(month)
    return jsonify(data)


# ---------------------------------------------------------------------------
# Media Studio API
# ---------------------------------------------------------------------------

@api.route("/media/photos", methods=["GET"])
@_require_auth
def api_media_photos():
    import media_studio
    return jsonify({"photos": media_studio.list_photos()})


@api.route("/media/upload", methods=["POST"])
@_require_auth
def api_media_upload():
    import media_studio
    if "photos" not in request.files:
        return jsonify({"error": "No se recibieron fotos"}), 400
    files = request.files.getlist("photos")
    property_name = request.form.get("property", "")
    results = []
    for f in files:
        if not f.filename:
            continue
        try:
            meta = media_studio.save_photo(f, property_name=property_name)
            results.append(meta)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    return jsonify({"uploaded": results})


@api.route("/media/photos/<photo_id>", methods=["DELETE"])
@_require_auth
def api_media_delete_photo(photo_id):
    import media_studio
    if media_studio.delete_photo(photo_id):
        return jsonify({"ok": True})
    return jsonify({"error": "Foto no encontrada"}), 404


@api.route("/media/usage")
@_require_auth
def api_media_usage():
    return jsonify(analytics.get_media_usage())


@api.route("/media/purchase", methods=["POST"])
@_require_auth
def api_media_purchase():
    """Create payment checkout — MercadoPago (ARS) or Lemon Squeezy (USD)."""
    import payments

    data = request.get_json(silent=True) or {}
    count = data.get("count", 1)
    currency = data.get("currency", "ARS")
    if not isinstance(count, int) or count < 1:
        return jsonify({"error": "Cantidad invalida"}), 400

    try:
        if currency == "USD":
            result = payments.create_ls_checkout(count)
        else:
            result = payments.create_mp_checkout(count)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502


@api.route("/media/generate/video", methods=["POST"])
@_require_auth
def api_media_generate_video():
    import media_studio

    if not _get_google_ai_key():
        return jsonify({"error": "GOOGLE_AI_API_KEY no configurada. Configura la API key de Google AI en Railway."}), 400

    # Check usage limit first
    usage = analytics.get_media_usage()
    if usage["remaining"] <= 0:
        return jsonify({
            "error": f"Llegaste al limite de {usage['total_allowed']} videos este mes. Compra videos adicionales a ${usage['extra_video_price_ars']:,} ARS cada uno.",
            "limit_reached": True,
            "usage": usage,
        }), 429

    data = request.get_json(silent=True) or {}
    photo_ids = data.get("photo_ids", [])
    prompt = data.get("prompt", "")
    voiceover_text = data.get("voiceover_text", "")
    property_name = data.get("property", "")
    video_format = data.get("video_format", "vertical")
    voice = data.get("voice", "")
    enhance = data.get("enhance", True)

    if not photo_ids:
        return jsonify({"error": "Selecciona al menos una foto"}), 400

    # Resolve photo IDs to paths
    all_photos = {p["id"]: p["path"] for p in media_studio.list_photos()}
    paths = []
    for pid in photo_ids:
        if pid not in all_photos:
            return jsonify({"error": f"Foto {pid} no encontrada"}), 404
        paths.append(all_photos[pid])

    # Increment usage (double-check limit atomically)
    if not analytics.increment_video_usage():
        return jsonify({
            "error": "Limite de videos alcanzado",
            "limit_reached": True,
            "usage": analytics.get_media_usage(),
        }), 429

    job_id = media_studio.generate_video_tour(
        paths,
        prompt=prompt,
        voiceover_text=voiceover_text,
        property_name=property_name,
        video_format=video_format,
        voice=voice,
        enhance=enhance,
    )
    return jsonify({"job_id": job_id, "status": "queued", "usage": analytics.get_media_usage()})


@api.route("/media/generate/image", methods=["POST"])
@_require_auth
def api_media_generate_image():
    import media_studio
    if not _get_google_ai_key():
        return jsonify({"error": "GOOGLE_AI_API_KEY no configurada"}), 400
    data = request.get_json(silent=True) or {}
    prompt = data.get("prompt", "")
    property_name = data.get("property", "")
    if not prompt:
        return jsonify({"error": "Se necesita un prompt"}), 400
    job_id = media_studio.generate_image(prompt=prompt, property_name=property_name)
    return jsonify({"job_id": job_id, "status": "queued"})


@api.route("/media/jobs")
@_require_auth
def api_media_jobs():
    import media_studio
    return jsonify({"jobs": media_studio.list_jobs()})


@api.route("/media/jobs/<job_id>")
@_require_auth
def api_media_job_status(job_id):
    import media_studio
    job = media_studio.get_job(job_id)
    if not job:
        return jsonify({"error": "Job no encontrado"}), 404
    return jsonify(job)



@api.route("/media/share", methods=["POST"])
@_require_auth
def api_media_share():
    """Share a media file to WhatsApp (sends to agent's number)."""
    import whatsapp
    from config import NOTIFY_NUMBER, BASE_URL

    data = request.get_json(silent=True) or {}
    media_url = data.get("url", "")
    media_type = data.get("type", "video")  # video or image
    caption = data.get("caption", "")

    if not media_url:
        return jsonify({"error": "URL de media requerida"}), 400

    # Build full URL if relative
    if media_url.startswith("/"):
        base = BASE_URL.rstrip("/") if BASE_URL else request.host_url.rstrip("/")
        full_url = base + media_url
    else:
        full_url = media_url

    # Send to agent via WhatsApp
    msg = f"Media Studio — {caption or 'Nuevo contenido generado'}\n{full_url}"
    success = whatsapp.send_message(NOTIFY_NUMBER, msg)

    if success:
        return jsonify({"ok": True, "message": "Enviado a tu WhatsApp"})
    return jsonify({"error": "Error enviando a WhatsApp"}), 502
