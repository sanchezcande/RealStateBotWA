"""
Payment integrations: MercadoPago (ARS) + Lemon Squeezy (USD).
Creates checkout sessions and processes webhook notifications.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os

import mercadopago
import requests as http_requests
from flask import Blueprint, request, jsonify

import analytics

logger = logging.getLogger(__name__)

payments_bp = Blueprint("payments", __name__, url_prefix="/api/payments")

EXTRA_VIDEO_PRICE_USD = 25


# ---------------------------------------------------------------------------
# Shared: email notification to owner
# ---------------------------------------------------------------------------

def _notify_owner(info: dict):
    """Send payment notification email to the owner."""
    try:
        import smtplib
        from email.mime.text import MIMEText
        owner_email = os.environ.get("OWNER_EMAIL", "sanchezgcandelaria@gmail.com")
        smtp_host = os.environ.get("SMTP_HOST", "")
        smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        smtp_user = os.environ.get("SMTP_USER", "")
        smtp_pass = os.environ.get("SMTP_PASS", "")
        if not (owner_email and smtp_host):
            return
        amount_fmt = f"${info['amount']:,.0f} ARS" if info["currency"] == "ARS" else f"USD {info['amount']}"
        provider = info.get("provider", "")
        msg = MIMEText(
            f"Proveedor: {provider}\n"
            f"Videos: {info['count']}\n"
            f"Monto: {amount_fmt}\n"
            f"Email cliente: {info.get('payer_email') or 'no disponible'}\n"
            f"Payment ID: {info['payment_id']}"
        )
        msg["Subject"] = f"Nuevo pago - {info['count']} video(s) - {amount_fmt}"
        msg["From"] = smtp_user
        msg["To"] = owner_email
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        logger.info("Payment notification sent to %s", owner_email)
    except Exception as e:
        logger.error("Failed to send payment email: %s", e)


def _credit_and_notify(info: dict):
    """Credit videos and notify owner. Shared by both providers."""
    # Idempotency
    existing = analytics.get_payment(info["payment_id"])
    if existing and existing["status"] == "approved":
        logger.info("Payment %s already processed, skipping", info["payment_id"])
        return False

    analytics.record_payment(
        payment_id=info["payment_id"],
        provider=info.get("provider", ""),
        status=info["status"],
        amount=info["amount"],
        currency=info["currency"],
        video_count=info["count"],
        payer_email=info.get("payer_email", ""),
        external_ref=info.get("external_ref", ""),
    )

    if info["status"] == "approved":
        analytics.add_purchased_videos(info["count"])
        logger.info("Payment %s approved — credited %d videos", info["payment_id"], info["count"])
        _notify_owner(info)
        return True

    return False


# ===========================================================================
# MercadoPago (ARS)
# ===========================================================================

def _get_mp_sdk() -> mercadopago.SDK:
    token = os.environ.get("MP_ACCESS_TOKEN", "")
    if not token:
        raise ValueError("MP_ACCESS_TOKEN no configurado")
    return mercadopago.SDK(token)


def create_mp_checkout(count: int) -> dict:
    """Create a MercadoPago checkout preference."""
    sdk = _get_mp_sdk()
    price_ars = int(os.environ.get("EXTRA_VIDEO_PRICE_ARS", "25000"))
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


@payments_bp.route("/mercadopago/webhook", methods=["POST"])
def mp_webhook():
    """Receive MercadoPago IPN notifications."""
    data = request.get_json(silent=True) or {}
    if not data:
        data = request.args.to_dict()

    logger.info("MP webhook received: %s", json.dumps(data)[:500])

    try:
        topic = data.get("topic") or data.get("type", "")
        payment_id = None

        if topic == "payment":
            payment_id = data.get("data", {}).get("id")
        if not payment_id:
            payment_id = data.get("data", {}).get("id")
        if not payment_id:
            return jsonify({"ok": True}), 200

        sdk = _get_mp_sdk()
        result = sdk.payment().get(int(payment_id))
        if result["status"] != 200:
            logger.error("MP payment().get failed: %s", result)
            return jsonify({"ok": True}), 200

        payment = result["response"]
        external_ref = payment.get("external_reference", "")
        try:
            ref_data = json.loads(external_ref)
            count = ref_data.get("count", 1)
        except (json.JSONDecodeError, TypeError):
            count = 1

        _credit_and_notify({
            "payment_id": str(payment_id),
            "provider": "mercadopago",
            "status": "approved" if payment.get("status") == "approved" else payment.get("status", ""),
            "count": count,
            "amount": payment.get("transaction_amount", 0),
            "currency": payment.get("currency_id", "ARS"),
            "payer_email": payment.get("payer", {}).get("email", ""),
            "external_ref": external_ref,
        })
    except Exception as e:
        logger.error("MP webhook error: %s", e)

    return jsonify({"ok": True}), 200


# ===========================================================================
# Lemon Squeezy (USD)
# ===========================================================================

def _ls_headers() -> dict:
    api_key = os.environ.get("LEMONSQUEEZY_API_KEY", "")
    if not api_key:
        raise ValueError("LEMONSQUEEZY_API_KEY no configurado")
    return {
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
        "Authorization": f"Bearer {api_key}",
    }


def create_ls_checkout(count: int) -> dict:
    """Create a Lemon Squeezy checkout for USD video purchases."""
    store_id = os.environ.get("LEMONSQUEEZY_STORE_ID", "")
    variant_id = os.environ.get("LEMONSQUEEZY_VARIANT_ID", "")
    base_url = os.environ.get("BASE_URL", "")

    if not store_id or not variant_id:
        raise ValueError("LEMONSQUEEZY_STORE_ID y LEMONSQUEEZY_VARIANT_ID requeridos")

    total_cents = EXTRA_VIDEO_PRICE_USD * 100 * count

    payload = {
        "data": {
            "type": "checkouts",
            "attributes": {
                "custom_price": total_cents,
                "product_options": {
                    "redirect_url": f"{base_url}/dashboard/media?payment=success",
                },
                "checkout_data": {
                    "custom": {
                        "video_count": str(count),
                    },
                },
            },
            "relationships": {
                "store": {
                    "data": {"type": "stores", "id": store_id}
                },
                "variant": {
                    "data": {"type": "variants", "id": variant_id}
                },
            },
        }
    }

    resp = http_requests.post(
        "https://api.lemonsqueezy.com/v1/checkouts",
        headers=_ls_headers(),
        json=payload,
        timeout=15,
    )

    if resp.status_code in (200, 201):
        data = resp.json()
        checkout_url = data["data"]["attributes"]["url"]
        checkout_id = data["data"]["id"]

        analytics.record_payment(
            payment_id=checkout_id,
            provider="lemonsqueezy",
            status="pending",
            amount=EXTRA_VIDEO_PRICE_USD * count,
            currency="USD",
            video_count=count,
        )
        return {
            "checkout_url": checkout_url,
            "checkout_id": checkout_id,
            "total_usd": EXTRA_VIDEO_PRICE_USD * count,
            "count": count,
        }

    logger.error("LS checkout creation failed: %s %s", resp.status_code, resp.text[:300])
    raise RuntimeError("Error creando checkout de Lemon Squeezy")


def _verify_ls_signature(payload: bytes, signature: str) -> bool:
    """Verify Lemon Squeezy webhook signature."""
    secret = os.environ.get("LEMONSQUEEZY_WEBHOOK_SECRET", "")
    if not secret:
        logger.warning("LEMONSQUEEZY_WEBHOOK_SECRET not set, skipping verification")
        return True
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@payments_bp.route("/lemonsqueezy/webhook", methods=["POST"])
def ls_webhook():
    """Receive Lemon Squeezy webhook notifications."""
    raw_body = request.get_data()
    signature = request.headers.get("X-Signature", "")

    if not _verify_ls_signature(raw_body, signature):
        logger.warning("LS webhook signature mismatch")
        return jsonify({"ok": False}), 401

    data = request.get_json(silent=True) or {}
    event = data.get("meta", {}).get("event_name", "")

    logger.info("LS webhook received: %s", event)

    if event != "order_created":
        return jsonify({"ok": True}), 200

    try:
        custom_data = data.get("meta", {}).get("custom_data", {})
        count = int(custom_data.get("video_count", 1))

        attrs = data.get("data", {}).get("attributes", {})
        order_id = str(data.get("data", {}).get("id", ""))
        amount = attrs.get("total", 0) / 100  # cents to dollars
        status = attrs.get("status", "")
        email = attrs.get("user_email", "")

        is_paid = status in ("paid", "complete")

        _credit_and_notify({
            "payment_id": order_id,
            "provider": "lemonsqueezy",
            "status": "approved" if is_paid else status,
            "count": count,
            "amount": amount,
            "currency": "USD",
            "payer_email": email,
            "external_ref": json.dumps(custom_data),
        })
    except Exception as e:
        logger.error("LS webhook error: %s", e)

    return jsonify({"ok": True}), 200


# ---------------------------------------------------------------------------
# Payment history (dashboard)
# ---------------------------------------------------------------------------

@payments_bp.route("/history")
def payment_history():
    """List payment history (requires dashboard auth)."""
    from dashboard_api import _require_auth

    @_require_auth
    def _inner():
        return jsonify(analytics.get_payments_list())

    return _inner()
