"""
MercadoPago payment integration for video purchases.
Creates checkout preferences and processes webhook notifications.
"""
from __future__ import annotations

import json
import logging
import os

import mercadopago
from flask import Blueprint, request, jsonify

import analytics

logger = logging.getLogger(__name__)

payments_bp = Blueprint("payments", __name__, url_prefix="/api/payments")

def _get_sdk() -> mercadopago.SDK:
    token = os.environ.get("MP_ACCESS_TOKEN", "")
    if not token:
        raise ValueError("MP_ACCESS_TOKEN no configurado")
    return mercadopago.SDK(token)


# ---------------------------------------------------------------------------
# Checkout creation (called from dashboard_api)
# ---------------------------------------------------------------------------

def create_video_checkout(count: int) -> dict:
    """Create a MercadoPago checkout preference for video purchases."""
    sdk = _get_sdk()
    price_ars = int(os.environ.get("EXTRA_VIDEO_PRICE_ARS", "35385"))
    base_url = os.environ.get("BASE_URL", "")
    total = price_ars * count

    notification_url = f"{base_url}/api/payments/mercadopago/webhook" if base_url else ""

    preference_data: dict = {
        "items": [
            {
                "title": f"Videos extra x{count} — RealStateBot",
                "quantity": count,
                "unit_price": price_ars,
                "currency_id": "ARS",
            }
        ],
        "external_reference": json.dumps({"type": "video_purchase", "count": count}),
        "back_urls": {
            "success": f"{base_url}/dashboard/media?payment=success",
            "failure": f"{base_url}/dashboard/media?payment=failure",
            "pending": f"{base_url}/dashboard/media?payment=pending",
        },
        "auto_return": "approved",
    }

    if notification_url:
        preference_data["notification_url"] = notification_url

    result = sdk.preference().create(preference_data)

    if result["status"] == 201:
        resp = result["response"]
        # Store preference so we can reconcile later
        analytics.record_payment(
            payment_id=resp["id"],
            provider="mercadopago",
            status="pending",
            amount=total,
            currency="ARS",
            video_count=count,
            external_ref=resp.get("external_reference", ""),
        )
        return {
            "checkout_url": resp["init_point"],
            "preference_id": resp["id"],
            "total_ars": total,
            "count": count,
        }

    logger.error("MP preference creation failed: %s", result)
    raise RuntimeError("Error creando checkout de MercadoPago")


# ---------------------------------------------------------------------------
# Webhook (called by MercadoPago — no auth required)
# ---------------------------------------------------------------------------

def _process_notification(data: dict) -> dict | None:
    """Verify payment with MP API and return info if approved."""
    topic = data.get("topic") or data.get("type", "")
    payment_id = None

    if topic == "payment":
        payment_id = data.get("data", {}).get("id")
    elif topic in ("merchant_order", "test"):
        return None

    if not payment_id:
        payment_id = data.get("data", {}).get("id")

    if not payment_id:
        return None

    sdk = _get_sdk()
    result = sdk.payment().get(int(payment_id))

    if result["status"] != 200:
        logger.error("MP payment().get failed for %s: %s", payment_id, result)
        return None

    payment = result["response"]
    status = payment.get("status", "")
    external_ref = payment.get("external_reference", "")

    try:
        ref_data = json.loads(external_ref)
        count = ref_data.get("count", 1)
    except (json.JSONDecodeError, TypeError):
        count = 1

    return {
        "payment_id": str(payment_id),
        "status": status,
        "count": count,
        "amount": payment.get("transaction_amount", 0),
        "currency": payment.get("currency_id", "ARS"),
        "payer_email": payment.get("payer", {}).get("email", ""),
        "external_ref": external_ref,
    }


@payments_bp.route("/mercadopago/webhook", methods=["POST"])
def mp_webhook():
    """Receive MercadoPago IPN notifications."""
    data = request.get_json(silent=True) or {}
    query_params = request.args.to_dict()

    # MP can send data in body or query params
    if not data and query_params:
        data = query_params

    logger.info("MP webhook received: %s", json.dumps(data)[:500])

    try:
        info = _process_notification(data)
    except Exception as e:
        logger.error("MP webhook processing error: %s", e)
        return jsonify({"ok": True}), 200  # Always 200 so MP doesn't retry forever

    if not info:
        return jsonify({"ok": True}), 200

    # Idempotency: check if already processed
    existing = analytics.get_payment(info["payment_id"])
    if existing and existing["status"] == "approved":
        logger.info("Payment %s already processed, skipping", info["payment_id"])
        return jsonify({"ok": True}), 200

    # Record/update payment
    analytics.record_payment(
        payment_id=info["payment_id"],
        provider="mercadopago",
        status=info["status"],
        amount=info["amount"],
        currency=info["currency"],
        video_count=info["count"],
        payer_email=info["payer_email"],
        external_ref=info["external_ref"],
    )

    # Credit videos only when approved
    if info["status"] == "approved":
        analytics.add_purchased_videos(info["count"])
        logger.info("Payment %s approved — credited %d videos", info["payment_id"], info["count"])

    return jsonify({"ok": True}), 200


@payments_bp.route("/history")
def payment_history():
    """List payment history (requires dashboard auth)."""
    from dashboard_api import _require_auth

    @_require_auth
    def _inner():
        return jsonify(analytics.get_payments_list())

    return _inner()
