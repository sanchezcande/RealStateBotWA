"""
Dashboard JSON API Blueprint.
All endpoints require token auth (query param or session).
"""
from __future__ import annotations

import os
from functools import wraps

from flask import Blueprint, request, jsonify, session

import analytics
from config import DASHBOARD_PLAN

api = Blueprint("dashboard_api", __name__, url_prefix="/api/dashboard")


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
