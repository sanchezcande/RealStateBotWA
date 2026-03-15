"""
Dashboard HTML routes Blueprint.
Serves the rendered template pages with plan-based feature gating.
"""
from __future__ import annotations

import csv
import io
import os
import logging
from functools import wraps

from flask import (
    Blueprint, request, render_template, redirect,
    url_for, session, flash,
)

import analytics
from config import (
    DASHBOARD_PLAN, DASHBOARD_TOKEN, BRANCH_NAME,
    DASHBOARD_SECRET_KEY, DASHBOARD_ADMIN_PASSWORD,
)

logger = logging.getLogger(__name__)

dashboard = Blueprint(
    "dashboard", __name__,
    url_prefix="/dashboard",
    template_folder="templates",
)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _get_token():
    return request.args.get("token", "") or session.get("dashboard_token", "")


def _require_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get("dashboard_auth"):
            return f(*args, **kwargs)
        token = _get_token()
        expected = os.environ.get("DASHBOARD_TOKEN", "")
        if expected and token == expected:
            return f(*args, **kwargs)
        # If a token was provided but doesn't match, return 403 (backward compat)
        if token:
            return "Acceso denegado.", 403
        # No token and no session — return 403 if token auth is configured,
        # otherwise redirect to login
        if expected and not DASHBOARD_ADMIN_PASSWORD:
            return "Acceso denegado.", 403
        return redirect(url_for("dashboard.login"))
    return wrapper


def _ctx(**extra):
    """Build common template context."""
    return {
        "plan": DASHBOARD_PLAN,
        "token": _get_token(),
        "branch": BRANCH_NAME,
        **extra,
    }


# ---------------------------------------------------------------------------
# Login / Logout
# ---------------------------------------------------------------------------

@dashboard.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        # Also support token-only access (legacy URL)
        token = request.args.get("token", "")
        expected = os.environ.get("DASHBOARD_TOKEN", "")
        if expected and token == expected:
            session["dashboard_auth"] = True
            session["dashboard_token"] = token
            return redirect(url_for("dashboard.index"))
        return render_template("dashboard/login.html")

    # POST — password login
    password = request.form.get("password", "")
    token = request.form.get("token", "")
    expected_token = os.environ.get("DASHBOARD_TOKEN", "")
    expected_pass = DASHBOARD_ADMIN_PASSWORD

    if (expected_pass and password == expected_pass) or (expected_token and token == expected_token):
        session["dashboard_auth"] = True
        session["dashboard_token"] = expected_token
        return redirect(url_for("dashboard.index"))

    return render_template("dashboard/login.html", error="Credenciales incorrectas")


@dashboard.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("dashboard.login"))


# ---------------------------------------------------------------------------
# Main Dashboard
# ---------------------------------------------------------------------------

@dashboard.route("")
@_require_auth
def index():
    if DASHBOARD_PLAN == "starter":
        return render_template("dashboard/upgrade.html", **_ctx(active_page="dashboard"))

    days = request.args.get("days", 30, type=int)
    if days not in (7, 30, 90):
        days = 30
    data = analytics.get_dashboard_data(days=days)
    if not data:
        return "Error cargando datos.", 500

    return render_template(
        "dashboard/index.html",
        **_ctx(
            active_page="dashboard",
            days=days,
            kpis=data["kpis"],
            conv_by_day=data["conv_by_day"],
            peak_hours=data["peak_hours"],
            top_properties=data["top_properties"],
            op_split=data["op_split"],
            channel_split=data["channel_split"],
            channel_breakdown=data["channel_breakdown"],
            escalation_split=data["escalation_split"],
            lead_quality_split=data["lead_quality_split"],
            period_comparison=data["period_comparison"],
        ),
    )


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

@dashboard.route("/conversations")
@_require_auth
def conversations_page():
    if DASHBOARD_PLAN == "starter":
        return render_template("dashboard/upgrade.html", **_ctx(active_page="conversations"))
    return render_template("dashboard/conversations.html", **_ctx(active_page="conversations"))


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------

@dashboard.route("/leads")
@_require_auth
def leads_page():
    if DASHBOARD_PLAN == "starter":
        return render_template("dashboard/upgrade.html", **_ctx(active_page="leads"))
    return render_template("dashboard/leads.html", **_ctx(active_page="leads"))


# ---------------------------------------------------------------------------
# Visits
# ---------------------------------------------------------------------------

@dashboard.route("/visits")
@_require_auth
def visits_page():
    if DASHBOARD_PLAN == "starter":
        return render_template("dashboard/upgrade.html", **_ctx(active_page="visits"))
    return render_template("dashboard/visits.html", **_ctx(active_page="visits"))


# ---------------------------------------------------------------------------
# Media Studio
# ---------------------------------------------------------------------------

@dashboard.route("/media")
@_require_auth
def media_page():
    if DASHBOARD_PLAN == "starter":
        return render_template("dashboard/upgrade.html", **_ctx(active_page="media"))
    return render_template("dashboard/media.html", **_ctx(active_page="media"))


# ---------------------------------------------------------------------------
# CSV Export
# ---------------------------------------------------------------------------

@dashboard.route("/export.csv")
@_require_auth
def export_csv():
    if DASHBOARD_PLAN != "premium":
        return "Exportacion CSV disponible solo en el plan Premium.", 403
    days = request.args.get("days", 30, type=int)
    if days not in (7, 30, 90):
        days = 30
    try:
        from datetime import datetime, timedelta
        cutoff = (datetime.now(analytics.AR_TZ) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
        with analytics._db_lock:
            conn = analytics._get_conn()
            rows = conn.execute(
                """SELECT phone_hash, channel, first_seen_at, last_seen_at,
                          message_count, became_lead, visit_count, operation, property_type
                   FROM conversations
                   WHERE last_seen_at >= ?
                   ORDER BY last_seen_at DESC""",
                (cutoff,),
            ).fetchall()
    except Exception as e:
        logger.error("CSV export error: %s", e)
        return "Error generando el CSV.", 500

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id_anonimo", "canal", "primer_contacto", "ultimo_contacto",
        "mensajes", "lead_calificado", "visitas", "operacion", "tipo_propiedad",
    ])
    writer.writerows(rows)
    branch_slug = BRANCH_NAME.lower().replace(" ", "_") if BRANCH_NAME else "valentina"
    filename = f"{branch_slug}_leads_{days}d.csv"
    return output.getvalue(), 200, {
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": f"attachment; filename={filename}",
    }
