"""
Daily conversation auditor — runs inside the app via APScheduler.
Audits recent conversations using the same LLM API the bot uses,
and sends an email report to the dashboard owner.
"""
import json
import logging
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from openai import OpenAI

import analytics
from config import (
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL,
    AR_TZ, OWNER_EMAIL,
)

logger = logging.getLogger(__name__)

_client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

AUDIT_PROMPT = """Sos un auditor de calidad de un bot inmobiliario llamado Vera.
Te voy a pasar una conversacion real entre Vera (assistant) y un cliente (user).
Los mensajes marcados como [AGENTE HUMANO] fueron escritos por un asesor humano, no por Vera.

Analizá la conversación y buscá estos errores:

1. PREGUNTA REDUNDANTE: Vera pregunta algo que el cliente o el agente ya respondió.
2. CONTEXTO IGNORADO: Vera ignora info obvia del mensaje.
3. NOMBRE MAL USADO: Vera no usa el nombre del cliente cuando ya lo sabe, o lo pregunta de nuevo.
4. CONTINUIDAD ROTA: Vera no sigue el hilo de lo que dijo el agente humano.
5. INFO INVENTADA: Vera dice algo que no puede saber o inventa datos.
6. RESPUESTA GENERICA: Vera da una respuesta vaga cuando tenía info para ser específica.
7. OPORTUNIDAD PERDIDA: Vera no ofrece fotos, no propone visita, o no avanza la conversación cuando debería.
8. TONO ROBÓTICO: Vera suena como bot en vez de como asesora real.
9. FOTOS NO ENVIADAS: Vera promete fotos pero no las manda, o el cliente las pide y no llegan.

Respondé SOLO en JSON con esta estructura:
{
  "score": 1-10,
  "errors": [
    {"type": "PREGUNTA REDUNDANTE", "message_index": 3, "detail": "descripción corta del error"}
  ],
  "summary": "resumen de 1 línea de la calidad general"
}

Si no hay errores, devolvé errors como array vacío y score alto.
NO incluyas texto fuera del JSON."""


def _format_thread(messages: list[dict]) -> str:
    """Format messages for the auditor LLM."""
    lines = []
    for i, m in enumerate(messages):
        role = m.get("role", "unknown")
        content = m.get("content", "")
        if content.strip().startswith("[img:"):
            continue
        label = "CLIENTE" if role == "user" else ("AGENTE HUMANO" if role == "agent" else "VERA")
        lines.append(f"[{i}] [{label}] {content}")
    return "\n".join(lines)


def _audit_one(thread_text: str, name: str) -> dict | None:
    """Send one conversation to the LLM for audit."""
    try:
        resp = _client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": AUDIT_PROMPT},
                {"role": "user", "content": f"Conversación con: {name}\n\n{thread_text}"},
            ],
            temperature=0.1,
            max_tokens=1000,
        )
        content = resp.choices[0].message.content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(content)
    except Exception as e:
        logger.error("Audit LLM call failed for %s: %s", name, e)
        return None


def _send_audit_email(subject: str, body_html: str):
    """Send audit report via email."""
    smtp_host = os.environ.get("SMTP_HOST", "")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")
    to_email = OWNER_EMAIL

    if not (smtp_host and smtp_user and to_email):
        logger.warning("Audit email not sent: SMTP not configured or OWNER_EMAIL missing")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to_email
    msg.attach(MIMEText(body_html, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        logger.info("Audit email sent to %s", to_email)
    except Exception as e:
        logger.error("Failed to send audit email: %s", e)


def _build_email_html(audited: int, avg: float, all_errors: list, by_type: dict) -> str:
    """Build a clean HTML email for the audit report."""
    score_color = "#059669" if avg >= 7 else ("#E65100" if avg >= 5 else "#DC2626")

    error_rows = ""
    for err_type, errs in sorted(by_type.items(), key=lambda x: -len(x[1])):
        details = "".join(
            f'<li style="color:#525252;font-size:13px;margin:2px 0">{e["conversation"]}: {e["detail"]}</li>'
            for e in errs[:4]
        )
        error_rows += f"""
        <tr>
          <td style="padding:10px 14px;border-bottom:1px solid #f3f4f6;font-weight:600;font-size:13px;color:#1a1a1a;white-space:nowrap;vertical-align:top">{err_type}</td>
          <td style="padding:10px 14px;border-bottom:1px solid #f3f4f6;text-align:center;font-size:13px;color:#525252;vertical-align:top">{len(errs)}</td>
          <td style="padding:10px 14px;border-bottom:1px solid #f3f4f6;vertical-align:top"><ul style="margin:0;padding-left:16px">{details}</ul></td>
        </tr>"""

    no_errors_msg = ""
    if not all_errors:
        no_errors_msg = '<p style="color:#059669;font-size:14px;text-align:center;padding:20px">Sin errores detectados. Todas las conversaciones OK.</p>'

    return f"""
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:600px;margin:0 auto;background:#fff">
      <div style="padding:24px 0;border-bottom:1px solid #e5e7eb">
        <span style="font-size:11px;letter-spacing:2px;color:#9ca3af;text-transform:uppercase">PropBot</span>
        <h1 style="font-size:20px;font-weight:700;color:#1a1a1a;margin:6px 0 4px">Audit diario de Vera</h1>
        <p style="font-size:13px;color:#9ca3af;margin:0">{audited} conversaciones auditadas</p>
      </div>

      <div style="display:flex;gap:16px;padding:20px 0;border-bottom:1px solid #e5e7eb">
        <div style="flex:1;text-align:center">
          <div style="font-size:28px;font-weight:700;color:{score_color}">{avg:.1f}</div>
          <div style="font-size:11px;color:#9ca3af;letter-spacing:1px;text-transform:uppercase">Score</div>
        </div>
        <div style="flex:1;text-align:center">
          <div style="font-size:28px;font-weight:700;color:#1a1a1a">{len(all_errors)}</div>
          <div style="font-size:11px;color:#9ca3af;letter-spacing:1px;text-transform:uppercase">Errores</div>
        </div>
        <div style="flex:1;text-align:center">
          <div style="font-size:28px;font-weight:700;color:#1a1a1a">{audited}</div>
          <div style="font-size:11px;color:#9ca3af;letter-spacing:1px;text-transform:uppercase">Charlas</div>
        </div>
      </div>

      {no_errors_msg}

      {"" if not all_errors else f'''
      <table style="width:100%;border-collapse:collapse;margin-top:16px">
        <thead>
          <tr style="background:#fafaf9">
            <th style="padding:8px 14px;text-align:left;font-size:11px;color:#9ca3af;letter-spacing:1px;text-transform:uppercase;border-bottom:1px solid #e5e7eb">Error</th>
            <th style="padding:8px 14px;text-align:center;font-size:11px;color:#9ca3af;letter-spacing:1px;text-transform:uppercase;border-bottom:1px solid #e5e7eb">Cant</th>
            <th style="padding:8px 14px;text-align:left;font-size:11px;color:#9ca3af;letter-spacing:1px;text-transform:uppercase;border-bottom:1px solid #e5e7eb">Detalle</th>
          </tr>
        </thead>
        <tbody>
          {error_rows}
        </tbody>
      </table>
      '''}

      <div style="padding:20px 0;margin-top:16px;border-top:1px solid #e5e7eb;text-align:center">
        <a href="https://propbot.cc/dashboard/conversations" style="display:inline-block;padding:10px 24px;background:#ae6b51;color:#fff;text-decoration:none;border-radius:6px;font-size:13px;font-weight:600">Ver conversaciones</a>
      </div>

      <div style="padding:12px 0;text-align:center">
        <span style="font-size:11px;color:#9ca3af">Enviado por PropBot Audit System</span>
      </div>
    </div>"""


def run_daily_audit():
    """Run the daily audit. Called by APScheduler."""
    logger.info("Starting daily conversation audit...")
    try:
        _do_audit()
    except Exception as e:
        logger.error("Daily audit failed: %s", e, exc_info=True)


def _do_audit():
    """Core audit logic — fetch recent conversations, audit each, send email."""
    recent = analytics.get_recent_conversations(hours=24)
    if not recent:
        logger.info("Daily audit: no recent conversations to audit")
        return

    all_errors = []
    scores = []
    audited = 0

    for convo in recent:
        phone_hash = convo.get("phone_hash", "")
        name = convo.get("name") or phone_hash[:8]
        channel = convo.get("channel", "?")

        messages = analytics.load_messages_by_hash(phone_hash)
        if len(messages) < 3:
            continue

        thread_text = _format_thread(messages)
        result = _audit_one(thread_text, name)
        if not result:
            continue

        audited += 1
        score = result.get("score", 0)
        errors = result.get("errors", [])
        scores.append(score)

        for err in errors:
            err["conversation"] = name
            err["channel"] = channel
            all_errors.append(err)

        logger.info("Audit %s (%s): score=%d, errors=%d", name, channel, score, len(errors))

    if not scores:
        logger.info("Daily audit: no conversations with enough messages to audit")
        return

    avg = sum(scores) / len(scores)
    logger.info("Daily audit complete: %d convos, avg score %.1f, %d errors",
                audited, avg, len(all_errors))

    analytics.log_event("daily_audit", "system", channel="system",
                        score=round(avg, 1), errors=len(all_errors),
                        audited=audited)

    # Build and send email report
    by_type: dict[str, list] = {}
    for e in all_errors:
        by_type.setdefault(e["type"], []).append(e)

    score_label = "OK" if avg >= 7 else ("Revisar" if avg >= 5 else "ALERTA")
    subject = f"Vera Audit: {avg:.1f}/10 — {len(all_errors)} error(es) — {score_label}"
    body_html = _build_email_html(audited, avg, all_errors, by_type)
    _send_audit_email(subject, body_html)


def start(scheduler):
    """Register the daily audit job on the given APScheduler instance."""
    scheduler.add_job(
        run_daily_audit,
        "cron",
        hour=8,
        minute=0,
        id="daily_audit",
        replace_existing=True,
    )
    logger.info("Daily audit scheduler registered (runs at 08:00 AR)")
