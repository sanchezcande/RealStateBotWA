"""
Daily conversation auditor.
Fetches recent conversations from the PropBot dashboard API,
sends them to the LLM for quality analysis, and outputs a report
of errors / incongruencies found.

Usage:
  python audit_conversations.py

Required env vars:
  BOT_URL           - e.g. https://propbot.cc
  DASHBOARD_TOKEN   - dashboard auth token
  DEEPSEEK_API_KEY  - LLM API key

Optional:
  AUDIT_DAYS        - how many days back to audit (default: 1)
  DEEPSEEK_BASE_URL - LLM base URL (default: https://api.deepseek.com)
  DEEPSEEK_MODEL    - LLM model (default: deepseek-chat)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BOT_URL = os.environ.get("BOT_URL", "").rstrip("/")
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.openai.com/v1")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "gpt-4o-mini")
AUDIT_DAYS = int(os.environ.get("AUDIT_DAYS", "1"))

AUDIT_PROMPT = """Sos un auditor de calidad de un bot inmobiliario llamado Vera.
Te voy a pasar una conversacion real entre Vera (assistant/agent) y un cliente (user).
Los mensajes marcados como [AGENTE HUMANO] fueron escritos por un asesor humano, no por Vera.

Analizá la conversación y buscá estos errores:

1. PREGUNTA REDUNDANTE: Vera pregunta algo que el cliente o el agente ya respondió (ej: preguntar "comprar o alquilar?" cuando ya se habló de alquiler).
2. CONTEXTO IGNORADO: Vera ignora info obvia del mensaje (ej: "recibo de sueldo" = alquiler, el cliente ya dijo su nombre pero Vera pregunta "con quién hablo?").
3. NOMBRE MAL USADO: Vera no usa el nombre del cliente cuando ya lo sabe, o lo pregunta de nuevo.
4. CONTINUIDAD ROTA: Vera no sigue el hilo de lo que dijo el agente humano.
5. INFO INVENTADA: Vera dice algo que no puede saber o inventa datos.
6. RESPUESTA GENERICA: Vera da una respuesta vaga cuando tenía info para ser específica.
7. OPORTUNIDAD PERDIDA: Vera no ofrece fotos, no propone visita, o no avanza la conversación cuando debería.
8. TONO ROBÓTICO: Vera suena como bot en vez de como asesora real.

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


def fetch_conversations() -> list[dict]:
    """Fetch conversation list from dashboard API."""
    url = f"{BOT_URL}/dashboard/api/conversations"
    all_convos = []
    page = 1
    while True:
        resp = requests.get(url, params={"token": DASHBOARD_TOKEN, "page": page, "per_page": 50}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items") or data.get("conversations") or []
        if not items:
            break
        all_convos.extend(items)
        if page >= data.get("pages", 1):
            break
        page += 1
    return all_convos


def fetch_thread(phone_hash: str) -> list[dict]:
    """Fetch full message thread for a conversation."""
    url = f"{BOT_URL}/dashboard/api/conversations/{phone_hash}"
    resp = requests.get(url, params={"token": DASHBOARD_TOKEN}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("messages") or data.get("thread") or []


def is_recent(convo: dict, days: int) -> bool:
    """Check if conversation has activity within the last N days."""
    last = convo.get("last_message_at") or convo.get("updated_at") or convo.get("timestamp") or ""
    if not last:
        return False
    try:
        if isinstance(last, (int, float)):
            dt = datetime.fromtimestamp(last)
        else:
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
                try:
                    dt = datetime.strptime(last[:26], fmt)
                    break
                except ValueError:
                    continue
            else:
                return True  # Can't parse, include it to be safe
        return dt >= datetime.now() - timedelta(days=days)
    except Exception:
        return True


def format_thread_for_audit(messages: list[dict]) -> str:
    """Format a message thread into readable text for the LLM."""
    lines = []
    for i, m in enumerate(messages):
        role = m.get("role", m.get("sender", "unknown"))
        content = m.get("content", m.get("body", m.get("text", "")))
        ts = m.get("timestamp", "")
        label = "CLIENTE" if role in ("user", "client") else ("AGENTE HUMANO" if role == "agent" else "VERA")
        lines.append(f"[{i}] [{label}] {content}")
    return "\n".join(lines)


def audit_thread(thread_text: str, convo_name: str) -> dict | None:
    """Send thread to LLM for quality audit."""
    try:
        resp = requests.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": DEEPSEEK_MODEL,
                "messages": [
                    {"role": "system", "content": AUDIT_PROMPT},
                    {"role": "user", "content": f"Conversación con: {convo_name}\n\n{thread_text}"},
                ],
                "temperature": 0.1,
                "max_tokens": 1000,
            },
            timeout=60,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        # Extract JSON from response
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(content)
    except Exception as e:
        print(f"  [!] LLM audit failed for {convo_name}: {e}")
        return None


def main():
    json_mode = "--json" in sys.argv

    if not BOT_URL or not DASHBOARD_TOKEN or not DEEPSEEK_API_KEY:
        print("Missing required env vars: BOT_URL, DASHBOARD_TOKEN, DEEPSEEK_API_KEY")
        sys.exit(1)

    if not json_mode:
        print(f"=== PropBot Conversation Audit ({AUDIT_DAYS}d) ===")
        print(f"URL: {BOT_URL}")
        print()

    convos = fetch_conversations()
    recent = [c for c in convos if is_recent(c, AUDIT_DAYS)]
    if not json_mode:
        print(f"Total conversations: {len(convos)}, recent ({AUDIT_DAYS}d): {len(recent)}")
        print()

    if not recent:
        if not json_mode:
            print("No recent conversations to audit.")
        elif json_mode:
            print(json.dumps({"conversations_audited": 0, "average_score": 10, "errors": []}))
        return

    all_errors = []
    scores = []

    for convo in recent:
        phone_hash = convo.get("phone_hash") or convo.get("id") or ""
        name = convo.get("name") or convo.get("client_name") or phone_hash[:12]
        channel = convo.get("channel", "?")

        messages = fetch_thread(phone_hash)
        if len(messages) < 2:
            continue

        thread_text = format_thread_for_audit(messages)
        if not json_mode:
            print(f"Auditing: {name} ({channel}, {len(messages)} msgs)...", end=" ")

        result = audit_thread(thread_text, name)
        if not result:
            continue

        score = result.get("score", 0)
        errors = result.get("errors", [])
        summary = result.get("summary", "")
        scores.append(score)

        if errors:
            if not json_mode:
                print(f"Score: {score}/10 - {len(errors)} error(s)")
            for err in errors:
                err["conversation"] = name
                err["channel"] = channel
                all_errors.append(err)
                if not json_mode:
                    print(f"    [{err['type']}] {err['detail']}")
        else:
            if not json_mode:
                print(f"Score: {score}/10 - OK")

    avg = sum(scores) / len(scores) if scores else 0

    if not json_mode:
        print()
        print("=" * 60)
        print(f"Conversations audited: {len(scores)}")
        print(f"Average score: {avg:.1f}/10")
        print(f"Total errors found: {len(all_errors)}")

    if all_errors and not json_mode:
        print()
        print("ERROR SUMMARY:")
        by_type = {}
        for e in all_errors:
            by_type.setdefault(e["type"], []).append(e)
        for err_type, errs in sorted(by_type.items(), key=lambda x: -len(x[1])):
            print(f"  {err_type}: {len(errs)}x")
            for e in errs:
                print(f"    - {e['conversation']}: {e['detail']}")

    # JSON output mode for piping to audit_fix.py
    if json_mode:
        print(json.dumps({
            "conversations_audited": len(scores),
            "average_score": round(avg, 1),
            "errors": all_errors,
        }, ensure_ascii=False))
        return

    # Exit with error if average score is below threshold
    if avg < 6 and scores:
        print()
        print(f"ALERT: Average score {avg:.1f} is below threshold (6.0)")
        sys.exit(1)


if __name__ == "__main__":
    main()
