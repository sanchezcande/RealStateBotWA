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
# Coupon validation
# ===========================================================================

def _load_coupons() -> dict:
    """Parse COUPONS env var into {code: percent} dict."""
    raw = os.environ.get("COUPONS", "")
    coupons = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if ":" not in entry:
            continue
        code, pct = entry.split(":", 1)
        try:
            coupons[code.strip().upper()] = int(pct.strip())
        except ValueError:
            pass
    return coupons


def validate_coupon(code: str) -> dict | None:
    """Return {"code": str, "percent": int} if valid, else None."""
    coupons = _load_coupons()
    code = code.strip().upper()
    if code in coupons:
        return {"code": code, "percent": coupons[code]}
    return None


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


# ===========================================================================
# Subscription checkout (MercadoPago)
# ===========================================================================

def create_subscription_checkout(plan: str, period: str = "anual",
                                  coupon_code: str = "", payer_email: str = "") -> dict:
    """Create a MercadoPago checkout for a PropBot subscription plan."""
    sdk = _get_mp_sdk()
    base_url = os.environ.get("BASE_URL", "")

    prices = {
        "anual": int(os.environ.get("PLAN_PREMIUM_ANUAL_ARS", "5900000")),
        "trimestral": int(os.environ.get("PLAN_PREMIUM_TRIMESTRAL_ARS", "1750000")),
    }
    plan_names = {
        "anual": "PropBot Premium — Anual",
        "trimestral": "PropBot Premium — 3 meses",
    }

    if period not in prices:
        raise ValueError(f"Periodo invalido: {period}")

    price = prices[period]
    discount_pct = 0

    if coupon_code:
        coupon = validate_coupon(coupon_code)
        if coupon:
            discount_pct = coupon["percent"]
            price = int(price * (100 - discount_pct) / 100)

    notification_url = f"{base_url}/api/payments/mercadopago/webhook" if base_url else ""

    external_ref = json.dumps({
        "type": "subscription",
        "plan": plan,
        "period": period,
        "coupon": coupon_code.upper() if coupon_code else "",
        "discount_pct": discount_pct,
    })

    preference_data: dict = {
        "items": [
            {
                "title": plan_names[period],
                "quantity": 1,
                "unit_price": price,
                "currency_id": "ARS",
            }
        ],
        "external_reference": external_ref,
        "back_urls": {
            "success": f"{base_url}/activar/{period}?status=ok",
            "failure": f"{base_url}/activar/{period}?status=error",
            "pending": f"{base_url}/activar/{period}?status=pendiente",
        },
        "auto_return": "approved",
        "payment_methods": {
            "installments": 3,
        },
    }

    if payer_email:
        preference_data["payer"] = {"email": payer_email}

    if notification_url:
        preference_data["notification_url"] = notification_url

    result = sdk.preference().create(preference_data)

    if result["status"] == 201:
        resp = result["response"]
        analytics.record_payment(
            payment_id=resp["id"],
            provider="mercadopago",
            status="pending",
            amount=price,
            currency="ARS",
            video_count=0,
            external_ref=external_ref,
        )
        return {
            "checkout_url": resp["init_point"],
            "preference_id": resp["id"],
            "plan": plan,
            "period": period,
            "original_price": prices[period],
            "final_price": price,
            "discount_pct": discount_pct,
        }

    logger.error("MP subscription preference failed: %s", result)
    raise RuntimeError("Error creando checkout de MercadoPago")


@payments_bp.route("/subscription/prices")
def api_subscription_prices():
    """Return current plan prices."""
    return jsonify({
        "anual": int(os.environ.get("PLAN_PREMIUM_ANUAL_ARS", "5900000")),
        "trimestral": int(os.environ.get("PLAN_PREMIUM_TRIMESTRAL_ARS", "1750000")),
    })


@payments_bp.route("/coupon/validate", methods=["POST"])
def api_validate_coupon():
    """Validate a coupon code. Returns discount percentage."""
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()
    if not code:
        return jsonify({"valid": False}), 200
    result = validate_coupon(code)
    if result:
        return jsonify({"valid": True, "code": result["code"], "percent": result["percent"]})
    return jsonify({"valid": False}), 200


@payments_bp.route("/subscription/checkout", methods=["POST"])
def api_subscription_checkout():
    """Create a subscription checkout."""
    data = request.get_json(silent=True) or {}
    plan = (data.get("plan") or "premium").strip().lower()
    period = (data.get("period") or "anual").strip().lower()
    coupon = (data.get("coupon") or "").strip()
    email = (data.get("email") or "").strip()

    if period not in ("anual", "trimestral"):
        return jsonify({"error": "Periodo invalido"}), 400

    try:
        result = create_subscription_checkout(plan, period=period, coupon_code=coupon, payer_email=email)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502


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
