"""
scripts/testar_run.py — Testa uma run real com o agente de análise.
Uso: python scripts/testar_run.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
os.environ.setdefault("SENTINELA_ROOT", str(_ROOT))

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env", override=False)

from ibm.iam import token_atual


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {token_atual()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def main() -> None:
    url_base = os.environ.get("WXO_SERVICE_URL", "").rstrip("/")
    agent_id = os.environ.get("WXO_AGENT_ANALISE_ID", "")

    # Busca primeira transação dos dados operacionais
    candidatas_path = _ROOT / "dados" / "mock" / "operacional" / "candidatas.json"
    with open(candidatas_path, encoding="utf-8") as f:
        candidatas = json.load(f)

    tx = candidatas[0]
    tx_id = tx.get("transacao_id") or tx.get("id") or tx.get("transacao", {}).get("id")
    print(f"Transacao: {tx_id}")
    print(f"Agent: {agent_id}")

    # Cria run
    payload = {
        "message": {"role": "user", "content": f"Analise a transacao {tx_id}."},
        "agent_id": agent_id,
        "context_variables": {
            "fraude_operacao_id": "op-test-001",
            "fraude_transacao_id": tx_id,
            "fraude_fase": "analise",
        },
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{url_base}/v1/orchestrate/runs", data=data, headers=_headers(), method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        run = json.loads(r.read())

    run_id = run["run_id"]
    thread_id = run["thread_id"]
    print(f"Run criada: run_id={run_id}, thread_id={thread_id}")

    # Aguarda conclusão
    headers_get = {k: v for k, v in _headers().items() if k != "Content-Type"}
    status_final = None
    for i in range(30):
        time.sleep(4)
        req2 = urllib.request.Request(
            f"{url_base}/v1/orchestrate/runs/{run_id}", headers=headers_get
        )
        with urllib.request.urlopen(req2, timeout=15) as r:
            status = json.loads(r.read())
        s = status.get("status", "")
        print(f"  [{i + 1:02d}] status={s}")
        if s in ("completed", "failed", "error", "cancelled"):
            status_final = status
            break

    if not status_final:
        print("TIMEOUT — run ainda em execução")
        return

    # Salva resultado completo
    result_path = _ROOT / ".wxo_inspect" / "run_result.json"
    result_path.parent.mkdir(exist_ok=True)
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(status_final, f, indent=2, ensure_ascii=False)
    print(f"\nResultado salvo em: {result_path}")

    # Extrai texto
    from ibm.orchestrate import extrair_json_resposta, extrair_resposta_texto

    texto = extrair_resposta_texto(status_final)
    print(f"\n=== Texto do agente ===\n{texto}\n")

    parsed = extrair_json_resposta(status_final)
    if parsed:
        print(f"=== JSON extraído ===\n{json.dumps(parsed, indent=2, ensure_ascii=False)}\n")

    # Step history
    result = status_final.get("result", {})
    msg = result.get("data", {}).get("message", {}) if isinstance(result, dict) else {}
    step_history = msg.get("step_history", [])
    print(f"=== step_history ({len(step_history)} passos) ===")
    for step in step_history:
        print(
            f"  type={step.get('type')} name={step.get('name', '')} status={step.get('status', '')}"
        )
        if step.get("output"):
            print(f"    output: {str(step['output'])[:200]}")


if __name__ == "__main__":
    main()
