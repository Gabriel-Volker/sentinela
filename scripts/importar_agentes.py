"""
scripts/importar_agentes.py — Importa/atualiza os dois agentes no watsonx Orchestrate.

Distingue criação de atualização. Não duplica recursos.
Usa REST direto (POST /v1/orchestrate/agents) com tools resolvidos para UUIDs.

Uso:
  python scripts/importar_agentes.py [--dry-run] [--force-update]

Requer:
  WXO_SERVICE_URL e WXO_API_KEY configurados no .env
  Ferramentas já importadas (WXO_TOOL_*_ID configurados)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
os.environ.setdefault("SENTINELA_ROOT", str(_ROOT))

from dotenv import load_dotenv, set_key

load_dotenv(_ROOT / ".env", override=False)


def _verificar_prereqs() -> None:
    url = os.environ.get("WXO_SERVICE_URL", "")
    key = os.environ.get("WXO_API_KEY", "")
    if not url or not key:
        print("ERRO: WXO_SERVICE_URL e WXO_API_KEY devem estar configurados no .env")
        sys.exit(1)


def _headers() -> dict[str, str]:
    from ibm.iam import token_atual

    return {
        "Authorization": f"Bearer {token_atual()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _url_base() -> str:
    return os.environ.get("WXO_SERVICE_URL", "").rstrip("/")


def _get_json(endpoint: str) -> dict | list:
    url = f"{_url_base()}{endpoint}"
    req = urllib.request.Request(url, headers=_headers(), method="GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _post_json(endpoint: str, payload: dict) -> dict:
    url = f"{_url_base()}{endpoint}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=_headers(), method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _put_json(endpoint: str, payload: dict) -> dict:
    url = f"{_url_base()}{endpoint}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=_headers(), method="PUT")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _patch_json(endpoint: str, payload: dict) -> dict:
    url = f"{_url_base()}{endpoint}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=_headers(), method="PATCH")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _carregar_spec_agente(arquivo: str) -> dict:
    from ibm_watsonx_orchestrate.utils.utils import yaml_safe_load

    caminho = _ROOT / "agentes" / arquivo
    with open(caminho, encoding="utf-8") as f:
        return yaml_safe_load(f)


def _resolver_tools(spec: dict) -> dict:
    """Substitui nomes de ferramentas pelos UUIDs configurados no .env."""
    mapa_nomes = {
        "consultar_contexto": os.environ.get("WXO_TOOL_CONSULTAR_CONTEXTO_ID", ""),
        "registrar_analise": os.environ.get("WXO_TOOL_REGISTRAR_ANALISE_ID", ""),
        "executar_decisao": os.environ.get("WXO_TOOL_EXECUTAR_DECISAO_ID", ""),
    }
    tools_resolvidas = []
    for t in spec.get("tools", []):
        uuid = mapa_nomes.get(t, t)
        if not uuid:
            print(
                f"  AVISO: ferramenta '{t}' sem UUID configurado no .env. Configure WXO_TOOL_*_ID."
            )
        else:
            tools_resolvidas.append(uuid)
    spec = {**spec, "tools": tools_resolvidas}
    return spec


def _buscar_agente_existente(nome: str) -> dict | None:
    """Busca um agente pelo nome via SDK. Retorna o dict ou None."""
    try:
        from ibm_cloud_sdk_core.authenticators import IAMAuthenticator
        from ibm_watsonx_orchestrate.client.agents.agent_client import AgentClient

        url = _url_base()
        key = os.environ.get("WXO_API_KEY", "")
        auth = IAMAuthenticator(apikey=key)
        client = AgentClient(base_url=url, authenticator=auth)
        items = client.get_draft_by_name(nome)
        if items:
            agente = items[0] if isinstance(items, list) else items
            return agente if isinstance(agente, dict) else {"id": getattr(agente, "id", None)}
    except Exception:
        pass
    return None


AGENTES = [
    ("analise.yaml", "sentinela_analise", "WXO_AGENT_ANALISE_ID"),
    ("validacao.yaml", "sentinela_validacao", "WXO_AGENT_VALIDACAO_ID"),
]


def main(dry_run: bool = False, force_update: bool = False) -> None:
    _verificar_prereqs()

    print(f"\n{'=' * 60}")
    print("  Importação de agentes — Sentinela")
    print(f"  Modo: {'DRY-RUN' if dry_run else 'REAL'}")
    print(f"{'=' * 60}\n")

    ids_registrados: dict[str, str] = {}

    for arquivo, nome_agente, var_env in AGENTES:
        print(f"[{nome_agente}]")
        try:
            spec = _carregar_spec_agente(arquivo)
        except Exception as exc:
            print(f"  ERRO ao carregar spec: {exc}")
            continue

        # Remove llm se nulo
        if spec.get("llm") in (None, "null", ""):
            spec.pop("llm", None)

        # Resolve tools para UUIDs
        spec = _resolver_tools(spec)

        # Verifica se já existe
        agente_existente = _buscar_agente_existente(nome_agente)

        if agente_existente:
            agente_id = agente_existente.get("id", "")
            if force_update:
                print(f"  Agente existe (id={agente_id}). Atualizando via REST…")
                if not dry_run:
                    try:
                        resp = _post_json(f"/v1/orchestrate/agents/{agente_id}", spec)
                        print(f"  Atualizado com sucesso: {resp}")
                    except urllib.error.HTTPError as exc:
                        body = exc.read().decode(errors="replace")
                        # Tenta PUT se POST falhar
                        try:
                            resp = _put_json(f"/v1/orchestrate/agents/{agente_id}", spec)
                            print(f"  Atualizado (PUT) com sucesso: {resp}")
                        except Exception as exc2:
                            print(f"  ERRO ao atualizar: {body[:200]}")
                            print(f"  PUT tambem falhou: {exc2}")
            else:
                print(f"  Agente ja existe (id={agente_id}). Use --force-update para atualizar.")
            ids_registrados[var_env] = agente_id
        else:
            print("  Agente nao existe. Criando via REST…")
            if not dry_run:
                try:
                    resp = _post_json("/v1/orchestrate/agents", spec)
                    agente_id = resp.get("id", "?")
                    print(f"  Criado com sucesso (id={agente_id}).")
                    ids_registrados[var_env] = agente_id
                except urllib.error.HTTPError as exc:
                    body = exc.read().decode(errors="replace")
                    print(f"  ERRO ao criar: HTTP {exc.code}: {body[:300]}")
                except Exception as exc:
                    print(f"  ERRO ao criar: {exc}")
            else:
                print("  [DRY-RUN] Criação simulada.")

    if not dry_run and ids_registrados:
        _sugerir_env(ids_registrados)

    print("\nConcluido.")


def _sugerir_env(ids: dict[str, str]) -> None:
    print("\n--- Adicione ao seu .env ---")
    for var, val in ids.items():
        print(f"{var}={val}")
    print("---")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Importar agentes no watsonx Orchestrate")
    parser.add_argument("--dry-run", action="store_true", help="Simula sem fazer alteracoes")
    parser.add_argument("--force-update", action="store_true", help="Atualiza agentes existentes")
    args = parser.parse_args()
    main(dry_run=args.dry_run, force_update=args.force_update)
