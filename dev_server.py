"""Minimal dev server to preview the landing page and dashboard locally."""
from flask import Flask, render_template, request, jsonify
from datetime import datetime, timedelta
import time

app = Flask(__name__)

def _ctx(active_page):
    return dict(v=int(time.time()), token="preview", branch="", plan="pro", active_page=active_page)

@app.route("/")
def landing():
    return render_template("landing.html", v=int(time.time()))

@app.route("/dashboard")
def preview_dashboard():
    """Dashboard preview with fake data for visual testing."""
    return render_template("dashboard/index.html",
        **_ctx("dashboard"),
        days=30,
        kpis={
            "total_conversations": 284,
            "total_leads": 47,
            "total_visits": 31,
            "conv_to_lead_pct": 16.5,
            "conv_to_visit_pct": 10.9,
            "avg_response_sec": 4.2,
        },
        period_comparison={"prev_convs": 231},
        conv_by_day={"labels": ["1 Mar","2 Mar","3 Mar","4 Mar","5 Mar","6 Mar","7 Mar","8 Mar","9 Mar","10 Mar","11 Mar","12 Mar","13 Mar","14 Mar"], "values": [8,12,9,15,11,7,14,18,10,13,16,9,12,20]},
        peak_hours={"labels": ["8h","9h","10h","11h","12h","13h","14h","15h","16h","17h","18h","19h","20h","21h","22h"], "values": [3,8,15,22,18,12,9,14,19,24,21,16,11,7,4]},
        op_split={"labels": ["Alquiler","Venta"], "values": [168, 116]},
        channel_split={"labels": ["WhatsApp","Instagram","Facebook"], "values": [189, 58, 37]},
        escalation_split={"labels": ["Resuelto por bot","Escalado a humano"], "values": [241, 43]},
        lead_quality_split={"labels": ["Caliente","Tibio","Frío"], "values": [12, 21, 14]},
        top_properties={"labels": ["Depto 2amb Palermo","Casa c/pileta Nordelta","PH Belgrano","Depto 3amb Recoleta","Local Microcentro"], "items": [{"address":"Thames 2340"},{"address":"Los Sauces 145"},{"address":"Av Cabildo 1820"},{"address":"Av Alvear 1500"},{"address":"Florida 720"}], "values": [28,22,18,15,11], "confirmed": [24,19,16,13,9], "cancelled": [4,3,2,2,2]},
        channel_breakdown=True,
    )

@app.route("/dashboard/conversations")
def preview_conversations():
    return render_template("dashboard/conversations.html", **_ctx("conversations"))

@app.route("/dashboard/leads")
def preview_leads():
    return render_template("dashboard/leads.html", **_ctx("leads"))

@app.route("/dashboard/visits")
def preview_visits():
    return render_template("dashboard/visits.html", **_ctx("visits"))

@app.route("/dashboard/media")
def preview_media():
    return render_template("dashboard/media.html", **_ctx("media"), photos=[], jobs=[])

# ── Mock API endpoints ──────────────────────────────────────────
_now = datetime.now()

MOCK_CONVS = [
    {"phone_hash": "a1b2c3", "name": "María López", "phone": "+5491155001234", "last_preview": "Hola, busco un 2 ambientes en Palermo", "last_message": (_now - timedelta(minutes=12)).isoformat(), "is_lead": True, "visit_count": 1, "score": "hot", "channel": "whatsapp"},
    {"phone_hash": "d4e5f6", "name": "Carlos Ruiz", "phone": "+5491166002345", "last_preview": "Tienen algo en Belgrano con cochera?", "last_message": (_now - timedelta(hours=2)).isoformat(), "is_lead": True, "visit_count": 0, "score": "warm", "channel": "instagram"},
    {"phone_hash": "g7h8i9", "name": "Ana García", "phone": "+5491177003456", "last_preview": "Buenas, quiero alquilar en Recoleta", "last_message": (_now - timedelta(hours=5)).isoformat(), "is_lead": False, "visit_count": 0, "score": None, "channel": "whatsapp"},
    {"phone_hash": "j0k1l2", "name": "Diego Fernández", "phone": "+5491188004567", "last_preview": "Me interesa la casa de Nordelta", "last_message": (_now - timedelta(days=1)).isoformat(), "is_lead": True, "visit_count": 2, "score": "hot", "channel": "facebook"},
    {"phone_hash": "m3n4o5", "name": "Lucía Martínez", "phone": "+5491199005678", "last_preview": "Cuánto sale el PH de Cabildo?", "last_message": (_now - timedelta(days=1, hours=3)).isoformat(), "is_lead": True, "visit_count": 0, "score": "warm", "channel": "whatsapp"},
    {"phone_hash": "p6q7r8", "name": "Martín Sosa", "phone": "+5491100006789", "last_preview": "Gracias, lo voy a pensar", "last_message": (_now - timedelta(days=3)).isoformat(), "is_lead": False, "visit_count": 0, "score": "cold", "channel": "instagram"},
]

MOCK_MESSAGES = [
    {"role": "user", "content": "Hola, busco un 2 ambientes en Palermo, hasta 200k USD", "time": (_now - timedelta(hours=1)).isoformat()},
    {"role": "assistant", "content": "¡Hola María! Tenemos varias opciones en Palermo. ¿Preferís luminoso a contrafrente o con balcón a la calle?", "time": (_now - timedelta(hours=1, minutes=-2)).isoformat()},
    {"role": "user", "content": "Con balcón, y si tiene pileta mejor", "time": (_now - timedelta(minutes=45)).isoformat()},
    {"role": "assistant", "content": "Perfecto. Te muestro 3 opciones que cumplen eso. La primera es Thames 2340, 2 amb con balcón aterrazado y amenities completos.", "time": (_now - timedelta(minutes=43)).isoformat()},
    {"role": "user", "content": "Ese me gusta, puedo ir a verlo?", "time": (_now - timedelta(minutes=12)).isoformat()},
    {"role": "assistant", "content": "¡Claro! Tengo disponibilidad mañana a las 10:00 o a las 15:30. ¿Cuál te queda mejor?", "time": (_now - timedelta(minutes=11)).isoformat()},
]

MOCK_LEADS = [
    {"phone_hash": "a1b2c3", "name": "María López", "phone": "+5491155001234", "operation": "comprar", "property_type": "Departamento", "budget": "USD 200.000", "timeline": "3 meses", "score": "hot", "channel": "WhatsApp", "message_count": 14, "visit_count": 1, "days_since_contact": 0, "updated_at": (_now - timedelta(minutes=12)).isoformat()},
    {"phone_hash": "d4e5f6", "name": "Carlos Ruiz", "phone": "+5491166002345", "operation": "comprar", "property_type": "Departamento", "budget": "USD 280.000", "timeline": "6 meses", "score": "warm", "channel": "Instagram", "message_count": 8, "visit_count": 0, "days_since_contact": 0, "updated_at": (_now - timedelta(hours=2)).isoformat()},
    {"phone_hash": "j0k1l2", "name": "Diego Fernández", "phone": "+5491188004567", "operation": "comprar", "property_type": "Casa", "budget": "USD 450.000", "timeline": "1 mes", "score": "hot", "channel": "Facebook", "message_count": 22, "visit_count": 2, "days_since_contact": 1, "updated_at": (_now - timedelta(days=1)).isoformat()},
    {"phone_hash": "m3n4o5", "name": "Lucía Martínez", "phone": "+5491199005678", "operation": "alquilar", "property_type": "PH", "budget": "$650.000/mes", "timeline": "Inmediato", "score": "warm", "channel": "WhatsApp", "message_count": 6, "visit_count": 0, "days_since_contact": 1, "updated_at": (_now - timedelta(days=1, hours=3)).isoformat()},
    {"phone_hash": "p6q7r8", "name": "Martín Sosa", "phone": "+5491100006789", "operation": "alquilar", "property_type": "Departamento", "budget": "$400.000/mes", "timeline": "2 meses", "score": "cold", "channel": "Instagram", "message_count": 3, "visit_count": 0, "days_since_contact": 3, "updated_at": (_now - timedelta(days=3)).isoformat()},
]

MOCK_VISITS = [
    {"id": 1, "visit_date": (_now + timedelta(days=1)).strftime("%Y-%m-%d"), "visit_time": "10:00", "property_title": "Depto 2amb Thames 2340", "client_name": "María López", "address": "Thames 2340, Palermo", "status": "confirmed", "channel": "WhatsApp"},
    {"id": 2, "visit_date": (_now + timedelta(days=1)).strftime("%Y-%m-%d"), "visit_time": "15:30", "property_title": "Casa c/pileta Nordelta", "client_name": "Diego Fernández", "address": "Los Sauces 145, Nordelta", "status": "confirmed", "channel": "Facebook"},
    {"id": 3, "visit_date": (_now + timedelta(days=2)).strftime("%Y-%m-%d"), "visit_time": "11:00", "property_title": "PH Belgrano", "client_name": "Carlos Ruiz", "address": "Av Cabildo 1820", "status": "confirmed", "channel": "Instagram"},
    {"id": 4, "visit_date": (_now - timedelta(days=2)).strftime("%Y-%m-%d"), "visit_time": "16:00", "property_title": "Casa c/pileta Nordelta", "client_name": "Diego Fernández", "address": "Los Sauces 145, Nordelta", "status": "completed", "channel": "Facebook"},
    {"id": 5, "visit_date": (_now - timedelta(days=3)).strftime("%Y-%m-%d"), "visit_time": "10:30", "property_title": "Depto 3amb Recoleta", "client_name": "Lucía Martínez", "address": "Av Alvear 1500", "status": "cancelled", "channel": "WhatsApp"},
]

@app.route("/api/dashboard/conversations")
def api_conversations():
    return jsonify({"items": MOCK_CONVS, "total": len(MOCK_CONVS)})

@app.route("/api/dashboard/conversations/<phone_hash>")
def api_conversation_detail(phone_hash):
    lead = next((l for l in MOCK_LEADS if l["phone_hash"] == phone_hash), None)
    return jsonify({"messages": MOCK_MESSAGES, "lead": lead})

@app.route("/api/dashboard/conversations/<phone_hash>/takeover")
def api_takeover(phone_hash):
    return jsonify({"paused": False})

@app.route("/api/dashboard/leads")
def api_leads():
    return jsonify({"items": MOCK_LEADS, "total": len(MOCK_LEADS)})

@app.route("/api/dashboard/visits")
def api_visits():
    return jsonify({"items": MOCK_VISITS, "total": len(MOCK_VISITS)})

@app.route("/api/dashboard/visits/calendar")
def api_visits_calendar():
    days = {}
    for v in MOCK_VISITS:
        d = v["visit_date"]
        if d not in days:
            days[d] = []
        days[d].append({"time": v["visit_time"], "property": v["property_title"], "client": v["client_name"], "status": v["status"]})
    return jsonify({"days": days})

@app.route("/api/contact", methods=["POST"])
def api_contact():
    data = request.get_json(silent=True) or {}
    print(f"[CONTACT FORM] {data}")
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(debug=True, port=5050)
