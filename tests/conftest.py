"""
Shared pytest fixtures for RealStateBotWA tests.
Sets environment variables BEFORE any project imports to avoid config.py crashes.
"""
import os
import sys

# --- Set required env vars BEFORE any project module is imported ---
os.environ.setdefault("WHATSAPP_TOKEN", "test-token-123")
os.environ.setdefault("PHONE_NUMBER_ID", "123456789")
os.environ.setdefault("VERIFY_TOKEN", "test-verify-token")
os.environ.setdefault("NOTIFY_NUMBER", "5491100001111")
os.environ.setdefault("DEEPSEEK_API_KEY", "test-deepseek-key")
os.environ.setdefault("ANALYTICS_DB_PATH", ":memory:")
os.environ.setdefault("DASHBOARD_TOKEN", "test-dashboard-token")
os.environ.setdefault("DASHBOARD_PLAN", "premium")
os.environ.setdefault("DASHBOARD_SECRET_KEY", "test-secret-key")
# Force empty to avoid .env leaking a password into tests
os.environ["DASHBOARD_ADMIN_PASSWORD"] = ""
os.environ.setdefault("GOOGLE_SHEET_ID", "")
os.environ.setdefault("GOOGLE_CREDENTIALS_JSON", "")
os.environ.setdefault("GOOGLE_CALENDAR_ID", "")
os.environ.setdefault("PAGE_ACCESS_TOKEN", "")

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset all module-level state between tests."""
    import conversations
    import analytics

    conversations._store.clear()

    # Reset analytics DB (fresh in-memory DB per test)
    old_conn = analytics._conn
    analytics._conn = None
    if old_conn:
        try:
            old_conn.close()
        except Exception:
            pass
    analytics._DB_PATH = ":memory:"
    analytics.init_db()

    yield

    conversations._store.clear()


@pytest.fixture
def flask_client():
    """Flask test client."""
    from app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
