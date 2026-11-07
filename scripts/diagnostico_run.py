"""
scripts/diagnostico_run.py — Testa a integração IBM diretamente e exibe o resultado bruto.

Uso:
  python scripts/diagnostico_run.py [TRANSACAO_ID]
"""

import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
os.environ.setdefault("SENTINELA_ROOT", str(_ROOT))

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env", override=False)

import logging

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")

transacao_id = sys.argv[1] if len(sys.argv) > 1 else "TX-ENBSYWPG"
agente_id = os.environ.get("WXO_AGENT_ANALISE_ID", "")
print(f"\n{'=' * 60}")
print(f"Diagnóstico de run — transacao_id: {transacao_id}")
print(f"Agente: {agente_id}")
print(f"{'=' * 60}\n")

if not agente_id:
    print("ERRO: WXO_AGENT_ANALISE_ID não configurado.")
    sys.exit(1)

from ibm.orchestrate import aguardar_run, criar_run, extrair_json_resposta, extrair_resposta_texto

# Contexto
ctx_vars = {
    "fraude_operacao_id": "OP-DIAGNOSTICO",
    "fraude_transacao_id": transacao_id,
    "fraude_fase": "analise",
}

# Prompt
mensagem = (
    f"Analise a transacao com ID exato: {transacao_id}\n\n"
    f"Passo 1: Chame consultar_contexto com transacao_id='{transacao_id}'.\n"
    "Passo 2: Classifique o risco como baixo, medio ou alto com base nos dados.\n"
    f"Passo 3: Chame registrar_analise com transacao_id='{transacao_id}' e "
    "analise_json contendo o JSON do contrato versao 2.\n\n"
    "Correspondencias: baixo->autorizar, medio->validar, alto->bloquear.\n\n"
    "Passo 4 (OBRIGATORIO): Apos chamar registrar_analise, responda APENAS com o "
    "JSON da analise, sem texto adicional."
)

print("Criando run...")
try:
    run_info = criar_run(agente_id, mensagem, None, ctx_vars)
    run_id = run_info["run_id"]
    print(f"Run criado: run_id={run_id}, thread_id={run_info.get('thread_id')}\n")
except Exception as exc:
    print(f"ERRO ao criar run: {exc}")
    sys.exit(1)

print("Aguardando run (até 75s)...")
try:
    run_status = aguardar_run(run_id, max_tentativas=25)
except Exception as exc:
    print(f"ERRO ao aguardar run: {exc}")
    sys.exit(1)

print(f"Status final: {run_status.get('status')}")
print("\n--- Run status completo (primeiros 2000 chars) ---")
print(json.dumps(run_status, ensure_ascii=False, default=str)[:2000])

print("\n--- Texto extraído ---")
texto = extrair_resposta_texto(run_status)
print(repr(texto))

print("\n--- JSON extraído ---")
dados = extrair_json_resposta(run_status)
print(json.dumps(dados, ensure_ascii=False, indent=2) if dados else "NENHUM JSON ENCONTRADO")
