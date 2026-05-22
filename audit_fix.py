"""
Auto-fixer: reads audit results and patches the system prompt in ai.py.

Usage:
  python audit_conversations.py --json > /tmp/audit.json
  python audit_fix.py /tmp/audit.json

Or piped:
  python audit_conversations.py --json | python audit_fix.py

Required env vars:
  DEEPSEEK_API_KEY
"""
from __future__ import annotations

import json
import os
import sys
import re
import subprocess

import requests

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.openai.com/v1")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "gpt-4o-mini")
AI_PY_PATH = os.path.join(os.path.dirname(__file__), "ai.py")

FIX_PROMPT = """Sos un ingeniero de prompts para un bot inmobiliario llamado Vera.

Te voy a dar:
1. El system prompt actual de Vera (contenido de SYSTEM_PROMPT_TEMPLATE en ai.py)
2. Una lista de errores encontrados al auditar conversaciones reales

Tu tarea es proponer cambios MÍNIMOS al system prompt para corregir los errores.

Reglas:
- Solo modificá lo necesario. No reescribas todo el prompt.
- Cada fix debe ser un reemplazo exacto: old_text -> new_text
- Si un error ya está cubierto por una regla existente pero el bot la ignora, reforzá la regla con lenguaje más fuerte.
- Si es un error nuevo, agregá una regla en la sección más relevante.
- NO elimines reglas existentes que funcionan bien.
- NO cambies el formato general del prompt.
- Sé conciso en las reglas nuevas.

Respondé SOLO en JSON:
{
  "fixes": [
    {
      "error_type": "PREGUNTA REDUNDANTE",
      "old_text": "texto exacto a reemplazar en el prompt",
      "new_text": "texto nuevo",
      "reason": "por qué este cambio arregla el error"
    }
  ],
  "no_fix_needed": ["error_type que no requiere cambio en el prompt y por qué"]
}

Si no hay nada que fixear, devolvé fixes como array vacío.
NO incluyas texto fuera del JSON."""


def extract_prompt_template() -> str:
    """Extract SYSTEM_PROMPT_TEMPLATE from ai.py."""
    with open(AI_PY_PATH, "r") as f:
        content = f.read()
    match = re.search(r'SYSTEM_PROMPT_TEMPLATE\s*=\s*"""(.*?)"""', content, re.DOTALL)
    if not match:
        print("Could not extract SYSTEM_PROMPT_TEMPLATE from ai.py")
        sys.exit(1)
    return match.group(1)


def get_audit_data() -> dict:
    """Read audit JSON from file arg or stdin."""
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        with open(sys.argv[1]) as f:
            return json.load(f)
    else:
        return json.load(sys.stdin)


def ask_llm_for_fixes(prompt_template: str, errors: list[dict]) -> dict:
    """Ask LLM to propose fixes for the errors found."""
    error_summary = json.dumps(errors, ensure_ascii=False, indent=2)

    resp = requests.post(
        f"{DEEPSEEK_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": FIX_PROMPT},
                {"role": "user", "content": f"SYSTEM PROMPT ACTUAL:\n\n{prompt_template}\n\n---\n\nERRORES ENCONTRADOS:\n\n{error_summary}"},
            ],
            "temperature": 0.1,
            "max_tokens": 2000,
        },
        timeout=90,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"].strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(content)


def apply_fixes(fixes: list[dict]) -> int:
    """Apply fixes to ai.py by doing string replacements."""
    with open(AI_PY_PATH, "r") as f:
        content = f.read()

    applied = 0
    for fix in fixes:
        old = fix["old_text"]
        new = fix["new_text"]
        if old == new:
            continue
        if old in content:
            content = content.replace(old, new, 1)
            applied += 1
            print(f"  Applied: {fix['error_type']} - {fix['reason']}")
        else:
            print(f"  Skipped (old_text not found): {fix['error_type']}")

    if applied:
        with open(AI_PY_PATH, "w") as f:
            f.write(content)

    return applied


def git_commit_and_push(n_fixes: int):
    """Commit and push the changes."""
    subprocess.run(["git", "add", "ai.py"], check=True)
    msg = f"fix(vera): auto-fix {n_fixes} conversation error(s) from daily audit\n\nCo-Authored-By: PropBot Auditor <noreply@propbot.cc>"
    subprocess.run(["git", "commit", "-m", msg], check=True)
    subprocess.run(["git", "push"], check=True)
    print(f"Pushed {n_fixes} fix(es) to remote.")


def main():
    if not DEEPSEEK_API_KEY:
        print("Missing DEEPSEEK_API_KEY")
        sys.exit(1)

    audit = get_audit_data()
    errors = audit.get("errors", [])

    if not errors:
        print("No errors to fix.")
        return

    print(f"=== PropBot Auto-Fixer ===")
    print(f"Errors to analyze: {len(errors)}")
    print()

    # Group errors by type for cleaner analysis
    by_type = {}
    for e in errors:
        by_type.setdefault(e["type"], []).append(e)

    print("Error types:")
    for t, errs in sorted(by_type.items(), key=lambda x: -len(x[1])):
        print(f"  {t}: {len(errs)}x")
    print()

    prompt_template = extract_prompt_template()
    print("Asking LLM for fixes...")
    result = ask_llm_for_fixes(prompt_template, errors)

    fixes = result.get("fixes", [])
    no_fix = result.get("no_fix_needed", [])

    if no_fix:
        print(f"\nNo fix needed for: {', '.join(str(n) for n in no_fix)}")

    if not fixes:
        print("LLM determined no prompt changes are needed.")
        return

    print(f"\nApplying {len(fixes)} fix(es):")
    applied = apply_fixes(fixes)

    if applied:
        print(f"\n{applied} fix(es) applied to ai.py")
        # Only auto-push in CI
        if os.environ.get("CI"):
            git_commit_and_push(applied)
        else:
            print("Run locally — not pushing. Review changes with: git diff ai.py")
    else:
        print("No fixes could be applied (old_text not found in current prompt).")


if __name__ == "__main__":
    main()
