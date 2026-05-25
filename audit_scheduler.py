"""
Daily conversation auditor — runs inside the app via APScheduler.
Audits recent conversations using the same LLM API the bot uses,
and sends a WhatsApp summary to NOTIFY_NUMBER if errors are found.
"""
import json
import logging
import threading

from openai import OpenAI

import analytics
import whatsapp
from config import (
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL,
    NOTIFY_NUMBER, AR_TZ,
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
        # Skip image markers
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


def run_daily_audit():
    """Run the daily audit. Called by APScheduler."""
    logger.info("Starting daily conversation audit...")
    try:
        _do_audit()
    except Exception as e:
        logger.error("Daily audit failed: %s", e, exc_info=True)


def _do_audit():
    """Core audit logic — fetch recent conversations, audit each, notify."""
    # Get conversations with activity in the last 24h
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

        # Load messages from DB
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

    # Save audit results as analytics event
    analytics.log_event("daily_audit", "system", channel="system",
                        score=round(avg, 1), errors=len(all_errors),
                        audited=audited)

    # Send WhatsApp notification to NOTIFY_NUMBER
    if not NOTIFY_NUMBER:
        return

    if not all_errors:
        msg = f"Audit diario: {audited} conversaciones, score promedio {avg:.1f}/10. Sin errores."
        whatsapp.send_message(NOTIFY_NUMBER, msg)
        return

    # Group errors by type
    by_type: dict[str, list] = {}
    for e in all_errors:
        by_type.setdefault(e["type"], []).append(e)

    lines = [f"Audit diario: {audited} charlas, score {avg:.1f}/10, {len(all_errors)} error(es):\n"]
    for err_type, errs in sorted(by_type.items(), key=lambda x: -len(x[1])):
        lines.append(f"*{err_type}* ({len(errs)}x)")
        for e in errs[:3]:  # Max 3 examples per type
            lines.append(f"  - {e['conversation']}: {e['detail']}")
    if avg < 6:
        lines.append(f"\nScore bajo ({avg:.1f}). Revisar conversaciones en el dashboard.")

    whatsapp.send_message(NOTIFY_NUMBER, "\n".join(lines))


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
