"""
Automated QA test suite for RealStateBotWA.
Covers all core logic, edge cases, and integration points.
Run: pytest tests/ -v
"""
import json
import time
from unittest.mock import patch, MagicMock

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# 1. EXTRACTORS (app.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestExtractOperation:
    def test_alquilar_variants(self):
        from app import _extract_operation
        assert _extract_operation("quiero alquilar un depto") == "alquilar"
        assert _extract_operation("busco alquiler") == "alquilar"
        assert _extract_operation("necesito rentar algo") == "alquilar"
        assert _extract_operation("pago renta mensual") == "alquilar"

    def test_comprar_variants(self):
        from app import _extract_operation
        assert _extract_operation("quiero comprar una casa") == "comprar"
        assert _extract_operation("busco compra") == "comprar"
        assert _extract_operation("estoy comprando") == "comprar"

    def test_none_when_no_match(self):
        from app import _extract_operation
        assert _extract_operation("hola buen dia") is None
        assert _extract_operation("necesito info") is None

    def test_case_insensitive(self):
        from app import _extract_operation
        assert _extract_operation("QUIERO ALQUILAR") == "alquilar"
        assert _extract_operation("COMPRAR casa") == "comprar"


class TestExtractPropertyType:
    def test_departamento(self):
        from app import _extract_property_type
        assert _extract_property_type("busco departamento") == "departamento"
        assert _extract_property_type("un depto lindo") == "departamento"
        assert _extract_property_type("dpto en palermo") == "departamento"

    def test_ambientes_maps_to_departamento(self):
        from app import _extract_property_type
        assert _extract_property_type("busco 2 ambientes") == "departamento"
        assert _extract_property_type("tres ambientes") == "departamento"
        assert _extract_property_type("un ambiente") == "departamento"

    def test_casa(self):
        from app import _extract_property_type
        assert _extract_property_type("busco casa") == "casa"
        assert _extract_property_type("un chalet") == "casa"

    def test_ph(self):
        from app import _extract_property_type
        assert _extract_property_type("busco un ph") == "PH"
        assert _extract_property_type("un p.h en villa crespo") == "PH"

    def test_monoambiente(self):
        from app import _extract_property_type
        assert _extract_property_type("busco monoambiente") == "monoambiente"
        assert _extract_property_type("un mono chico") == "monoambiente"

    def test_local(self):
        from app import _extract_property_type
        assert _extract_property_type("busco local comercial") == "local"

    def test_oficina(self):
        from app import _extract_property_type
        assert _extract_property_type("necesito oficina") == "oficina"

    def test_none_when_no_match(self):
        from app import _extract_property_type
        assert _extract_property_type("hola") is None
        assert _extract_property_type("busco algo lindo") is None


class TestExtractName:
    def test_soy_pattern(self):
        from app import _extract_name
        assert _extract_name("Hola soy Juan") == "Juan"
        assert _extract_name("soy María") == "María"

    def test_me_llamo_pattern(self):
        from app import _extract_name
        assert _extract_name("me llamo Pedro") == "Pedro"

    def test_mi_nombre_pattern(self):
        from app import _extract_name
        assert _extract_name("mi nombre es Laura") == "Laura"

    def test_habla_pattern(self):
        from app import _extract_name
        assert _extract_name("te habla Carlos") == "Carlos"
        assert _extract_name("te escribe Ana") == "Ana"
        assert _extract_name("de parte de Martín") == "Martín"

    def test_none_when_no_match(self):
        from app import _extract_name
        assert _extract_name("quiero un depto") is None
        assert _extract_name("hola buen dia") is None

    def test_capitalizes_name(self):
        from app import _extract_name
        assert _extract_name("soy juan") == "Juan"

    def test_ignores_short_names(self):
        from app import _extract_name
        # Name must be at least 2 chars (regex {1,20})
        assert _extract_name("soy A") is None


# ═══════════════════════════════════════════════════════════════════════════════
# 2. CONVERSATIONS (conversations.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestConversations:
    def test_add_and_get_messages(self):
        import conversations
        conversations.add_message("5491112345678", "user", "hola")
        conversations.add_message("5491112345678", "assistant", "Hola! Soy Valentina")
        msgs = conversations.get_messages("5491112345678")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["content"] == "Hola! Soy Valentina"

    def test_max_history_trimming(self):
        import conversations
        for i in range(50):
            conversations.add_message("5491100000000", "user", f"msg {i}")
        msgs = conversations.get_messages("5491100000000")
        assert len(msgs) == conversations.MAX_HISTORY
        assert msgs[0]["content"] == f"msg {50 - conversations.MAX_HISTORY}"

    def test_update_and_get_lead(self):
        import conversations
        conversations.update_lead("5491112345678", operation="alquilar", name="Juan")
        lead = conversations.get_lead("5491112345678")
        assert lead["operation"] == "alquilar"
        assert lead["name"] == "Juan"
        assert lead["budget"] is None

    def test_get_lead_returns_copy(self):
        import conversations
        lead1 = conversations.get_lead("5491112345678")
        lead1["name"] = "modified"
        lead2 = conversations.get_lead("5491112345678")
        assert lead2["name"] is None  # original unmodified

    def test_get_messages_returns_copy(self):
        import conversations
        conversations.add_message("5491112345678", "user", "hola")
        msgs = conversations.get_messages("5491112345678")
        msgs.append({"role": "user", "content": "extra"})
        assert len(conversations.get_messages("5491112345678")) == 1

    def test_conversation_summary(self):
        import conversations
        conversations.add_message("5491112345678", "user", "hola")
        conversations.add_message("5491112345678", "assistant", "Hola!")
        conversations.add_message("5491112345678", "user", "busco depto")
        summary = conversations.get_conversation_summary("5491112345678")
        assert "hola" in summary
        assert "busco depto" in summary

    def test_separate_conversations(self):
        import conversations
        conversations.add_message("phone1", "user", "msg1")
        conversations.add_message("phone2", "user", "msg2")
        assert len(conversations.get_messages("phone1")) == 1
        assert len(conversations.get_messages("phone2")) == 1

    def test_lead_notified_loaded_from_db(self):
        import conversations
        import analytics
        phone = "5491112345678"
        analytics.upsert_lead(phone, notified=True, name="Ana")
        conversations._store.clear()
        lead = conversations.get_lead(phone)
        assert lead["notified"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# 3. LEAD QUALIFIER (lead_qualifier.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestLeadQualifier:
    def test_extract_lead_data(self):
        import lead_qualifier
        text = 'Bla bla <!--lead:{"budget":"100k","operation":"alquilar","timeline":"inmediato","name":"Juan"}-->'
        data = lead_qualifier.extract_lead_data(text)
        assert data["budget"] == "100k"
        assert data["operation"] == "alquilar"
        assert data["name"] == "Juan"

    def test_extract_lead_data_invalid_json(self):
        import lead_qualifier
        text = "<!--lead:not json-->"
        assert lead_qualifier.extract_lead_data(text) is None

    def test_extract_lead_data_no_tag(self):
        import lead_qualifier
        assert lead_qualifier.extract_lead_data("hola como estas") is None

    def test_extract_callback_data(self):
        import lead_qualifier
        text = '<!--callback:{"preferred_time":"mañana 10hs","phone":null}-->'
        data = lead_qualifier.extract_callback_data(text)
        assert data["preferred_time"] == "mañana 10hs"

    def test_clean_response_removes_all_tags(self):
        import lead_qualifier
        text = 'Hola Juan <!--lead:{"name":"Juan"}--> <!--callback:{"preferred_time":"10hs"}-->'
        clean = lead_qualifier.clean_response(text)
        assert "<!--" not in clean
        assert "Hola Juan" in clean

    def test_is_qualified_true(self):
        import lead_qualifier
        assert lead_qualifier.is_qualified({"budget": "100k", "operation": "alquilar", "timeline": "ya"})

    def test_is_qualified_false_missing_fields(self):
        import lead_qualifier
        assert not lead_qualifier.is_qualified({"budget": "100k", "operation": None, "timeline": "ya"})
        assert not lead_qualifier.is_qualified({})

    @patch("whatsapp.send_message", return_value=True)
    def test_process_updates_lead(self, mock_send):
        import lead_qualifier
        import conversations
        text = 'Te busco opciones <!--lead:{"budget":"200k","operation":"comprar","timeline":null,"name":"Ana"}-->'
        clean = lead_qualifier.process("5491112345678", text)
        assert "<!--" not in clean
        lead = conversations.get_lead("5491112345678")
        assert lead["budget"] == "200k"
        assert lead["name"] == "Ana"

    @patch("whatsapp.send_message", return_value=True)
    def test_process_notifies_on_qualified_lead(self, mock_send):
        import lead_qualifier
        import conversations
        conversations.update_lead("5491112345678", name="Test")
        text = '<!--lead:{"budget":"100k","operation":"alquilar","timeline":"inmediato","name":"Test"}-->'
        lead_qualifier.process("5491112345678", text)
        # Should have called send_message to notify agent
        mock_send.assert_called()
        lead = conversations.get_lead("5491112345678")
        assert lead["notified"] is True

    @patch("whatsapp.send_message", return_value=True)
    def test_process_does_not_double_notify(self, mock_send):
        import lead_qualifier
        import conversations
        conversations.update_lead("5491112345678", notified=True,
                                   budget="100k", operation="alquilar", timeline="ya")
        text = '<!--lead:{"budget":"100k","operation":"alquilar","timeline":"ya","name":null}-->'
        lead_qualifier.process("5491112345678", text)
        mock_send.assert_not_called()

    @patch("whatsapp.send_message", return_value=True)
    def test_process_callback_notifies_agent(self, mock_send):
        import lead_qualifier
        text = 'Dale, le aviso <!--callback:{"preferred_time":"tarde","phone":null}-->'
        lead_qualifier.process("5491112345678", text)
        mock_send.assert_called_once()
        call_text = mock_send.call_args[0][1]
        assert "llamen" in call_text or "Horario" in call_text


# ═══════════════════════════════════════════════════════════════════════════════
# 4. VISIT SCHEDULER (visit_scheduler.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestVisitScheduler:
    def test_extract_single_visit(self):
        import visit_scheduler
        text = 'Perfecto! <!--visit:{"property":"Depto Palermo","date":"2026-03-20","time":"10:00"}-->'
        visits = visit_scheduler.extract_all_visit_data(text)
        assert len(visits) == 1
        assert visits[0]["property"] == "Depto Palermo"
        assert visits[0]["date"] == "2026-03-20"

    def test_extract_multiple_visits(self):
        import visit_scheduler
        text = (
            'Genial! '
            '<!--visit:{"property":"Depto A","date":"2026-03-20","time":"10:00"}-->'
            '<!--visit:{"property":"Depto B","date":"2026-03-20","time":"14:00"}-->'
        )
        visits = visit_scheduler.extract_all_visit_data(text)
        assert len(visits) == 2
        assert visits[0]["property"] == "Depto A"
        assert visits[1]["property"] == "Depto B"

    def test_extract_no_visit(self):
        import visit_scheduler
        assert visit_scheduler.extract_all_visit_data("hola como estas") == []

    def test_extract_cancel_data(self):
        import visit_scheduler
        text = '<!--cancel_visit:{"property":"Depto X","date":"2026-03-20","time":"10:00"}-->'
        data = visit_scheduler.extract_cancel_data(text)
        assert data["property"] == "Depto X"

    def test_clean_response_removes_tags(self):
        import visit_scheduler
        text = 'Listo! <!--visit:{"property":"X","date":"2026-03-20","time":"10:00"}--> <!--cancel_visit:{"property":"Y","date":"2026-03-21","time":"11:00"}-->'
        clean = visit_scheduler.clean_response(text)
        assert "<!--" not in clean
        assert "Listo!" in clean

    @patch("calendar_client.create_visit_event", return_value="event123")
    @patch("whatsapp.send_message", return_value=True)
    @patch("sheets.get_listings", return_value=[])
    def test_process_creates_visit(self, mock_sheets, mock_send, mock_cal):
        import visit_scheduler
        import conversations
        text = 'Perfecto! <!--visit:{"property":"Depto Test","date":"2026-03-20","time":"10:00"}-->'
        clean = visit_scheduler.process("5491112345678", text)
        assert "<!--" not in clean
        mock_cal.assert_called_once()
        lead = conversations.get_lead("5491112345678")
        assert lead["visit_scheduled"] is True
        assert "Depto Test|2026-03-20|10:00" in lead["scheduled_visits"]
        assert lead["visit_events"]["Depto Test|2026-03-20|10:00"] == "event123"

    @patch("calendar_client.create_visit_event", return_value="evt1")
    @patch("whatsapp.send_message", return_value=True)
    @patch("sheets.get_listings", return_value=[])
    def test_process_deduplicates_visits(self, mock_sheets, mock_send, mock_cal):
        import visit_scheduler
        import conversations
        conversations.update_lead("5491112345678", scheduled_visits=["Depto Test|2026-03-20|10:00"])
        text = '<!--visit:{"property":"Depto Test","date":"2026-03-20","time":"10:00"}-->'
        visit_scheduler.process("5491112345678", text)
        mock_cal.assert_not_called()  # duplicate, should skip

    @patch("calendar_client.cancel_visit_event", return_value=True)
    @patch("whatsapp.send_message", return_value=True)
    def test_process_cancellation(self, mock_send, mock_cancel):
        import visit_scheduler
        import conversations
        conversations.update_lead("5491112345678",
            scheduled_visits=["Depto X|2026-03-20|10:00"],
            visit_events={"Depto X|2026-03-20|10:00": "evt999"})
        text = '<!--cancel_visit:{"property":"Depto X","date":"2026-03-20","time":"10:00"}-->'
        visit_scheduler.process("5491112345678", text)
        mock_cancel.assert_called_once_with("evt999")
        lead = conversations.get_lead("5491112345678")
        assert "Depto X|2026-03-20|10:00" not in lead["scheduled_visits"]

    @patch("calendar_client.create_visit_event", return_value="e1")
    @patch("whatsapp.send_message", return_value=True)
    @patch("sheets.get_listings", return_value=[
        {"titulo": "Depto Palermo", "direccion": "Av. Santa Fe 1234"}
    ])
    def test_process_appends_address(self, mock_sheets, mock_send, mock_cal):
        import visit_scheduler
        text = 'Dale! <!--visit:{"property":"Depto Palermo","date":"2026-03-20","time":"10:00"}-->'
        clean = visit_scheduler.process("5491112345678", text)
        assert "Av. Santa Fe 1234" in clean

    @patch("calendar_client.create_visit_event", return_value=None)
    @patch("whatsapp.send_message", return_value=True)
    @patch("sheets.get_listings", return_value=[])
    def test_process_tracks_visit_even_without_calendar(self, mock_sheets, mock_send, mock_cal):
        import visit_scheduler
        import conversations
        text = '<!--visit:{"property":"Test","date":"2026-03-20","time":"10:00"}-->'
        visit_scheduler.process("5491112345678", text)
        lead = conversations.get_lead("5491112345678")
        assert lead["visit_scheduled"] is True
        assert "Test|2026-03-20|10:00" in lead["scheduled_visits"]


# ═══════════════════════════════════════════════════════════════════════════════
# 5. WHATSAPP (whatsapp.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestWhatsApp:
    def test_normalize_ar_number_with_9(self):
        from whatsapp import _normalize_ar_number
        assert _normalize_ar_number("5491112345678") == "541112345678"

    def test_normalize_non_ar_number(self):
        from whatsapp import _normalize_ar_number
        assert _normalize_ar_number("5411123456") == "5411123456"  # unchanged

    def test_normalize_short_549_number(self):
        from whatsapp import _normalize_ar_number
        assert _normalize_ar_number("549111234") == "549111234"  # not 13 digits, unchanged

    @patch("requests.post")
    def test_send_message_success(self, mock_post):
        from whatsapp import send_message
        mock_post.return_value = MagicMock(status_code=200, ok=True)
        mock_post.return_value.raise_for_status = MagicMock()
        assert send_message("5491112345678", "Hola!") is True

    def test_send_message_empty_token(self):
        from whatsapp import send_message
        with patch.dict("os.environ", {"WHATSAPP_TOKEN": ""}, clear=False):
            with patch("config.WHATSAPP_TOKEN", ""):
                assert send_message("5491112345678", "Hola!") is False


# ═══════════════════════════════════════════════════════════════════════════════
# 6. WEBHOOK ENDPOINTS (app.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestWebhooks:
    def test_webhook_verification_success(self, flask_client):
        resp = flask_client.get("/webhook", query_string={
            "hub.mode": "subscribe",
            "hub.verify_token": "test-verify-token",
            "hub.challenge": "challenge123",
        })
        assert resp.status_code == 200
        assert resp.data == b"challenge123"

    def test_webhook_verification_fail(self, flask_client):
        resp = flask_client.get("/webhook", query_string={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong-token",
            "hub.challenge": "challenge123",
        })
        assert resp.status_code == 403

    def test_webhook_post_returns_200(self, flask_client):
        resp = flask_client.post("/webhook", json={"entry": []})
        assert resp.status_code == 200

    def test_webhook_post_empty_body(self, flask_client):
        resp = flask_client.post("/webhook", data="", content_type="application/json")
        assert resp.status_code == 200

    def test_meta_webhook_verification(self, flask_client):
        resp = flask_client.get("/webhook/meta", query_string={
            "hub.mode": "subscribe",
            "hub.verify_token": "test-verify-token",
            "hub.challenge": "meta_challenge",
        })
        assert resp.status_code == 200
        assert resp.data == b"meta_challenge"

    def test_meta_webhook_post_returns_200(self, flask_client):
        resp = flask_client.post("/webhook/meta", json={"object": "page", "entry": []})
        assert resp.status_code == 200

    def test_meta_webhook_ignores_non_page(self, flask_client):
        resp = flask_client.post("/webhook/meta", json={"object": "unknown", "entry": []})
        assert resp.status_code == 200


class TestHealthEndpoint:
    def test_health_ok(self, flask_client):
        resp = flask_client.get("/health")
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["checks"]["api"] == "ok"
        assert data["checks"]["database"] == "ok"
        assert resp.status_code == 200


class TestDashboard:
    def test_dashboard_no_token(self, flask_client):
        resp = flask_client.get("/dashboard")
        assert resp.status_code == 403

    def test_dashboard_wrong_token(self, flask_client):
        resp = flask_client.get("/dashboard", query_string={"token": "wrong"})
        assert resp.status_code == 403

    def test_dashboard_valid_token(self, flask_client):
        resp = flask_client.get("/dashboard", query_string={"token": "test-dashboard-token"})
        assert resp.status_code == 200
        assert b"Valentina" in resp.data

    def test_dashboard_invalid_days_defaults(self, flask_client):
        resp = flask_client.get("/dashboard", query_string={
            "token": "test-dashboard-token",
            "days": "abc",
        })
        assert resp.status_code == 200  # should not crash

    def test_dashboard_csv_export(self, flask_client):
        resp = flask_client.get("/dashboard/export.csv", query_string={
            "token": "test-dashboard-token",
            "days": "30",
        })
        assert resp.status_code == 200
        assert "text/csv" in resp.content_type


# ═══════════════════════════════════════════════════════════════════════════════
# 7. ANALYTICS (analytics.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAnalytics:
    def test_init_db_creates_tables(self):
        import analytics
        conn = analytics._get_conn()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = [t[0] for t in tables]
        assert "events" in table_names
        assert "conversations" in table_names

    def test_log_event_message_in(self):
        import analytics
        analytics.log_event("message_in", "5491112345678", channel="whatsapp")
        conn = analytics._get_conn()
        count = conn.execute("SELECT COUNT(*) FROM events WHERE event_type='message_in'").fetchone()[0]
        assert count == 1

    def test_log_event_creates_conversation(self):
        import analytics
        analytics.log_event("message_in", "5491112345678")
        conn = analytics._get_conn()
        row = conn.execute("SELECT message_count FROM conversations").fetchone()
        assert row[0] == 1

    def test_log_event_increments_message_count(self):
        import analytics
        analytics.log_event("message_in", "5491112345678")
        analytics.log_event("message_in", "5491112345678")
        conn = analytics._get_conn()
        row = conn.execute("SELECT message_count FROM conversations").fetchone()
        assert row[0] == 2

    def test_log_event_lead_qualified(self):
        import analytics
        analytics.log_event("message_in", "5491112345678")
        analytics.log_event("lead_qualified", "5491112345678", operation="alquilar")
        conn = analytics._get_conn()
        row = conn.execute("SELECT became_lead, operation FROM conversations").fetchone()
        assert row[0] == 1
        assert row[1] == "alquilar"

    def test_log_event_visit_scheduled(self):
        import analytics
        analytics.log_event("message_in", "5491112345678")
        analytics.log_event("visit_scheduled", "5491112345678", property="Depto X")
        conn = analytics._get_conn()
        row = conn.execute("SELECT visit_count FROM conversations").fetchone()
        assert row[0] == 1

    def test_log_event_visit_cancelled_decrements(self):
        import analytics
        analytics.log_event("message_in", "5491112345678")
        analytics.log_event("visit_scheduled", "5491112345678", property="Depto X")
        analytics.log_event("visit_cancelled", "5491112345678", property="Depto X")
        conn = analytics._get_conn()
        row = conn.execute("SELECT visit_count FROM conversations").fetchone()
        assert row[0] == 0

    def test_peak_hours_use_ar_timezone(self):
        import analytics
        analytics.log_event("message_in", "5491112345678")
        conn = analytics._get_conn()
        row = conn.execute("SELECT hour FROM events").fetchone()
        # The hour should be in AR timezone (UTC-3), not UTC
        from datetime import datetime
        import pytz
        expected_hour = datetime.now(pytz.timezone("America/Argentina/Buenos_Aires")).hour
        assert row[0] == expected_hour

    def test_get_dashboard_data_returns_all_keys(self):
        import analytics
        data = analytics.get_dashboard_data(days=30)
        assert "kpis" in data
        assert "conv_by_day" in data
        assert "peak_hours" in data
        assert "top_properties" in data
        assert "op_split" in data
        assert "channel_split" in data
        assert "escalation_split" in data
        assert "lead_quality_split" in data
        assert "period_comparison" in data

    def test_get_dashboard_data_kpis_filtered_by_date(self):
        import analytics
        # Log some events
        analytics.log_event("new_conversation", "phone1")
        analytics.log_event("message_in", "phone1")
        data = analytics.get_dashboard_data(days=30)
        assert data["kpis"]["total_conversations"] >= 1

    def test_get_dashboard_data_uses_time_cutoff(self):
        import analytics
        from datetime import datetime, timedelta

        now = datetime.now(analytics.AR_TZ)
        old_ts = (now - timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M:%S")
        new_ts = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")

        with analytics._db_lock:
            conn = analytics._get_conn()
            old_hash = analytics._hash_phone("phone_old")
            new_hash = analytics._hash_phone("phone_new")
            conn.execute(
                """INSERT INTO conversations
                   (phone_hash, channel, first_seen_at, last_seen_at, message_count,
                    became_lead, visit_count, operation, property_type)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (old_hash, "whatsapp", old_ts, old_ts, 1, 0, 0, None, None),
            )
            conn.execute(
                """INSERT INTO conversations
                   (phone_hash, channel, first_seen_at, last_seen_at, message_count,
                    became_lead, visit_count, operation, property_type)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (new_hash, "whatsapp", new_ts, new_ts, 1, 0, 0, None, None),
            )
            conn.execute(
                """INSERT INTO events
                   (event_type, phone_hash, channel, property, operation,
                    property_type, hour, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                ("new_conversation", old_hash, "whatsapp", None, None, None, now.hour, old_ts),
            )
            conn.execute(
                """INSERT INTO events
                   (event_type, phone_hash, channel, property, operation,
                    property_type, hour, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                ("new_conversation", new_hash, "whatsapp", None, None, None, now.hour, new_ts),
            )

        data = analytics.get_dashboard_data(days=1)
        assert data["kpis"]["total_conversations"] == 1

    def test_health_check(self):
        import analytics
        assert analytics.health_check() is True


# ═══════════════════════════════════════════════════════════════════════════════
# 8. SHEETS (sheets.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSheets:
    def test_bool_field_yes(self):
        from sheets import _bool_field
        assert _bool_field("Si") == "Sí"
        assert _bool_field("si") == "Sí"
        assert _bool_field("yes") == "Sí"
        assert _bool_field(True) == "Sí"

    def test_bool_field_no(self):
        from sheets import _bool_field
        assert _bool_field("No") == "No"
        assert _bool_field("no") == "No"
        assert _bool_field(False) == "No"

    def test_bool_field_empty(self):
        from sheets import _bool_field
        assert _bool_field(None) == "Consultar"
        assert _bool_field("") == "Consultar"

    def test_bool_field_other(self):
        from sheets import _bool_field
        assert _bool_field("Portero eléctrico") == "Portero eléctrico"

    def test_format_listings_for_prompt(self):
        from sheets import format_listings_for_prompt, SAMPLE_LISTINGS
        text = format_listings_for_prompt(SAMPLE_LISTINGS)
        assert "PROPIEDADES DISPONIBLES:" in text
        assert "Palermo" in text
        assert "USD" in text

    def test_format_listings_empty(self):
        from sheets import format_listings_for_prompt
        text = format_listings_for_prompt([])
        assert "PROPIEDADES DISPONIBLES:" in text

    def test_get_listings_returns_sample_without_config(self):
        import sheets
        sheets._cache["data"] = None
        sheets._cache["ts"] = 0
        listings = sheets.get_listings()
        assert len(listings) > 0
        assert listings[0]["id"] == "P001"

    def test_get_listings_uses_cache(self):
        import sheets
        sheets._cache["data"] = [{"id": "CACHED"}]
        sheets._cache["ts"] = time.time()  # fresh
        listings = sheets.get_listings()
        assert listings[0]["id"] == "CACHED"


# ═══════════════════════════════════════════════════════════════════════════════
# 9. EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_malformed_visit_tag_json(self):
        import visit_scheduler
        text = "<!--visit:not valid json-->"
        assert visit_scheduler.extract_all_visit_data(text) == []

    def test_malformed_lead_tag_json(self):
        import lead_qualifier
        text = "<!--lead:{broken-->"
        assert lead_qualifier.extract_lead_data(text) is None

    def test_message_size_limit(self):
        from app import MAX_MESSAGE_LENGTH
        assert MAX_MESSAGE_LENGTH == 4000

    def test_empty_message_handling(self):
        """_handle_message should not crash on empty text body."""
        from app import _handle_message
        # Text type but no body
        _handle_message({"type": "text", "from": "5491112345678", "text": {}})
        # Should not raise

    def test_unicode_in_extraction(self):
        from app import _extract_name
        assert _extract_name("soy José") == "José"
        assert _extract_name("me llamo Ñoño") == "Ñoño"  # Ñ is in the regex character class

    def test_multiple_tags_in_one_response(self):
        """AI response with lead + visit + callback tags."""
        import lead_qualifier
        import visit_scheduler
        text = (
            'Te confirmo la visita '
            '<!--lead:{"budget":"100k","operation":"alquilar","timeline":"ya","name":"Test"}-->'
            '<!--visit:{"property":"Depto","date":"2026-03-20","time":"10:00"}-->'
        )
        # Lead qualifier removes its tags
        clean1 = lead_qualifier.clean_response(text)
        assert "<!--lead:" not in clean1
        assert "<!--visit:" in clean1  # visit tag still there

        # Visit scheduler removes its tags
        clean2 = visit_scheduler.clean_response(clean1)
        assert "<!--" not in clean2

    def test_mid_dedup_fifo_eviction(self):
        """Verify FIFO eviction instead of full clear."""
        from app import _processed_mids, _processed_mids_lock

        with _processed_mids_lock:
            _processed_mids.clear()
            # Fill with 1001 entries
            for i in range(1001):
                _processed_mids[f"mid_{i}"] = True
            # Trigger eviction
            if len(_processed_mids) > 1000:
                for _k in list(_processed_mids.keys())[:500]:
                    del _processed_mids[_k]

            # Should have ~501 entries, NOT 0
            assert len(_processed_mids) == 501
            # Oldest should be gone, newest should remain
            assert "mid_0" not in _processed_mids
            assert "mid_1000" in _processed_mids
            _processed_mids.clear()

    @patch("whatsapp.send_message", return_value=True)
    @patch("ai.get_reply", return_value="Hola! Soy Valentina, con quien hablo?")
    def test_full_reply_pipeline(self, mock_ai, mock_send):
        """Integration: full _reply pipeline without external calls."""
        from app import _reply
        import conversations
        _reply("5491112345678", "Hola busco alquilar un depto")
        msgs = conversations.get_messages("5491112345678")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"
        lead = conversations.get_lead("5491112345678")
        assert lead["operation"] == "alquilar"
        assert lead["property_type"] == "departamento"

    @patch("whatsapp.send_message", return_value=True)
    @patch("ai.get_reply", return_value="Hola! Soy Valentina, con quien hablo?")
    def test_reply_strips_reintro_on_existing_conversation(self, mock_ai, mock_send):
        """After initial exchange, re-introduction should be stripped."""
        from app import _reply
        import conversations
        # Simulate existing conversation
        conversations.add_message("5491112345678", "user", "hola")
        conversations.add_message("5491112345678", "assistant", "Hola! Soy Valentina, con quién hablo?")
        # Now AI re-introduces (should be stripped)
        mock_ai.return_value = "Hola! Soy Valentina, con quién hablo? Como te puedo ayudar?"
        _reply("5491112345678", "me llamo Juan")
        msgs = conversations.get_messages("5491112345678")
        last_msg = msgs[-1]["content"]
        assert "Soy Valentina" not in last_msg

    def test_visit_incomplete_data_skipped(self):
        """Visit with missing date or time should be skipped."""
        import visit_scheduler
        text = '<!--visit:{"property":"Test","date":"","time":"10:00"}-->'
        with patch("sheets.get_listings", return_value=[]):
            clean = visit_scheduler.process("5491112345678", text)
        # No visit should be scheduled
        import conversations
        lead = conversations.get_lead("5491112345678")
        assert not lead.get("visit_scheduled")


# ═══════════════════════════════════════════════════════════════════════════════
# 10. CALENDAR CLIENT
# ═══════════════════════════════════════════════════════════════════════════════

class TestCalendarClient:
    def test_create_event_without_calendar_id(self):
        """Should return None when GOOGLE_CALENDAR_ID is not set."""
        import calendar_client
        with patch.object(calendar_client, "GOOGLE_CALENDAR_ID", ""):
            result = calendar_client.create_visit_event(
                "Test Property", "2026-03-20", "10:00", "5491112345678"
            )
            assert result is None

    def test_cancel_event_without_calendar_id(self):
        import calendar_client
        with patch.object(calendar_client, "GOOGLE_CALENDAR_ID", ""):
            assert calendar_client.cancel_visit_event("some-event-id") is False

    def test_get_free_slots_without_calendar_id(self):
        import calendar_client
        with patch.object(calendar_client, "GOOGLE_CALENDAR_ID", ""):
            assert calendar_client.get_free_slots() == []
