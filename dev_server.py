"""Minimal dev server to preview the landing page and dashboard locally."""
from flask import Flask, render_template
import time

app = Flask(__name__)

@app.route("/")
def landing():
    return render_template("landing.html", v=int(time.time()))

@app.route("/preview-dashboard")
def preview_dashboard():
    """Dashboard preview with fake data for visual testing."""
    return render_template("dashboard/index.html",
        v=int(time.time()),
        token="preview",
        branch="",
        plan="pro",
        active_page="dashboard",
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

if __name__ == "__main__":
    app.run(debug=True, port=5050)
