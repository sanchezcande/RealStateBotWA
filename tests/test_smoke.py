"""
Smoke tests para deployments en vivo.
Correr contra una instancia deployada para verificar que todo funciona.

Uso:
    BOT_URL=https://tu-bot.railway.app pytest tests/test_smoke.py -v
    BOT_URL=https://tu-bot.railway.app VERIFY_TOKEN=xxx DASHBOARD_TOKEN=xxx pytest tests/test_smoke.py -v
"""
import os
import pytest
import requests

BOT_URL = os.environ.get("BOT_URL", "").rstrip("/")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "")
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")

pytestmark = pytest.mark.skipif(not BOT_URL, reason="BOT_URL not set — skipping smoke tests")


class TestLiveHealth:
    def test_health_endpoint_ok(self):
        """Verifica que el bot responde y la DB está accesible."""
        resp = requests.get(f"{BOT_URL}/health", timeout=10)
        assert resp.status_code == 200, f"Health check failed: {resp.status_code} {resp.text}"
        data = resp.json()
        assert data["status"] == "ok", f"Health degraded: {data}"
        assert data["checks"]["database"] == "ok", "Database check failed"

    def test_health_response_time(self):
        """El health check debe responder en menos de 3 segundos."""
        resp = requests.get(f"{BOT_URL}/health", timeout=10)
        assert resp.elapsed.total_seconds() < 3, f"Health too slow: {resp.elapsed.total_seconds()}s"


class TestLiveWebhook:
    @pytest.mark.skipif(not VERIFY_TOKEN, reason="VERIFY_TOKEN not set")
    def test_webhook_verification(self):
        """Verifica que el webhook responde al challenge de Meta."""
        resp = requests.get(f"{BOT_URL}/webhook", params={
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "smoke_test_challenge",
        }, timeout=10)
        assert resp.status_code == 200
        assert resp.text == "smoke_test_challenge"

    def test_webhook_rejects_bad_token(self):
        resp = requests.get(f"{BOT_URL}/webhook", params={
            "hub.mode": "subscribe",
            "hub.verify_token": "definitely_wrong_token",
            "hub.challenge": "test",
        }, timeout=10)
        assert resp.status_code == 403

    def test_webhook_post_accepts_payload(self):
        """El webhook POST siempre debe retornar 200 (para que Meta no reintente)."""
        resp = requests.post(f"{BOT_URL}/webhook", json={"entry": []}, timeout=10)
        assert resp.status_code == 200


class TestLiveDashboard:
    @pytest.mark.skipif(not DASHBOARD_TOKEN, reason="DASHBOARD_TOKEN not set")
    def test_dashboard_accessible(self):
        resp = requests.get(f"{BOT_URL}/dashboard", params={
            "token": DASHBOARD_TOKEN,
        }, timeout=10)
        assert resp.status_code == 200
        assert "Vera" in resp.text

    def test_dashboard_rejects_no_token(self):
        resp = requests.get(f"{BOT_URL}/dashboard", timeout=10)
        assert resp.status_code == 403
