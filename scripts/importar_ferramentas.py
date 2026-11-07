"""
scripts/importar_ferramentas.py — Importa/atualiza as três ferramentas no watsonx Orchestrate.

Distingue criação de atualização. Não duplica recursos.
Registra os IDs no arquivo de configuração local quando necessário.

Uso:
  python scripts/importar_ferramentas.py [--dry-run] [--force-update]

Requer:
  WXO_SERVICE_URL e WXO_API_KEY configurados no .env
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Adiciona raiz ao path
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
os.environ.setdefault("SENTINELA_ROOT", str(_ROOT))

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env", override=False)


def _verificar_prereqs() -> None:
    url = os.environ.get("WXO_SERVICE_URL", "")
    key = os.environ.get("WXO_API_KEY", "")
    if not url or not key:
        print("ERRO: WXO_SERVICE_URL e WXO_API_KEY devem estar configurados no .env")
        sys.exit(1)


def _obter_cliente():
    """Cria um cliente autenticado do Orchestrate."""
    from ibm_cloud_sdk_core.authenticators import IAMAuthenticator
    from ibm_watsonx_orchestrate.client.tools.tool_client import ToolClient

    url = os.environ.get("WXO_SERVICE_URL", "").rstrip("/")
    key = os.environ.get("WXO_API_KEY", "")
    authenticator = IAMAuthenticator(apikey=key)
    return ToolClient(base_url=url, authenticator=authenticator)


def _listar_ferramentas_existentes(cliente) -> dict[str, dict]:
    """Retorna mapa nome -> ferramenta das ferramentas já importadas."""
    resultado: dict[str, dict] = {}
    for _, _fn_nome, nome_ferramenta in FERRAMENTAS:
        try:
            items = cliente.get_draft_by_name(nome_ferramenta)
            if items:
                resultado[nome_ferramenta] = items[0]
        except Exception:
            pass
    return resultado


def _spec_ferramenta(nome_modulo: str, nome_fn: str) -> dict:
    """Obtém o spec da ferramenta a partir do decorator @tool."""
    import importlib

    mod = importlib.import_module(f"ferramentas.{nome_modulo}")
    fn = getattr(mod, nome_fn)
    spec = fn.__tool_spec__
    return spec.model_dump(exclude_unset=True, exclude_none=True, mode="json")


FERRAMENTAS = [
    ("contexto", "consultar_contexto", "consultar_contexto"),
    ("analise", "registrar_analise", "registrar_analise"),
    ("decisao", "executar_decisao", "executar_decisao"),
]


def main(dry_run: bool = False, force_update: bool = False) -> None:
    _verificar_prereqs()

    print(f"\n{'=' * 60}")
    print("  Importação de ferramentas — Sentinela")
    print(f"  Modo: {'DRY-RUN' if dry_run else 'REAL'}")
    print(f"{'=' * 60}\n")

    cliente = _obter_cliente()
    existentes = _listar_ferramentas_existentes(cliente)

    ids_registrados: dict[str, str] = {}

    for modulo, fn_nome, nome_ferramenta in FERRAMENTAS:
        print(f"[{nome_ferramenta}]")
        try:
            spec = _spec_ferramenta(modulo, fn_nome)
        except Exception as exc:
            print(f"  ERRO ao obter spec: {exc}")
            continue

        if nome_ferramenta in existentes:
            ferramenta_existente = existentes[nome_ferramenta]
            ferramenta_id = ferramenta_existente.get("id", "")
            if force_update:
                print(f"  Ferramenta existe (id={ferramenta_id}). Atualizando…")
                if not dry_run:
                    try:
                        cliente.update(ferramenta_id, spec)
                        print("  Atualizada com sucesso.")
                    except Exception as exc:
                        print(f"  ERRO ao atualizar: {exc}")
            else:
                print(
                    f"  Ferramenta já existe (id={ferramenta_id}). Use --force-update para atualizar."
                )
            ids_registrados[nome_ferramenta] = ferramenta_id
        else:
            print("  Ferramenta não existe. Criando…")
            if not dry_run:
                try:
                    resp = cliente.create(spec)
                    ferramenta_id = resp.get("id", "?")
                    print(f"  Criada com sucesso (id={ferramenta_id}).")
                    ids_registrados[nome_ferramenta] = ferramenta_id
                except Exception as exc:
                    print(f"  ERRO ao criar: {exc}")
            else:
                print("  [DRY-RUN] Criação simulada.")

    if not dry_run and ids_registrados:
        _sugerir_env(ids_registrados)

    print("\nConcluído.")


def _sugerir_env(ids: dict[str, str]) -> None:
    print("\n--- Adicione ao seu .env ---")
    mapa = {
        "consultar_contexto": "WXO_TOOL_CONSULTAR_CONTEXTO_ID",
        "registrar_analise": "WXO_TOOL_REGISTRAR_ANALISE_ID",
        "executar_decisao": "WXO_TOOL_EXECUTAR_DECISAO_ID",
    }
    for nome, var in mapa.items():
        if nome in ids:
            print(f"{var}={ids[nome]}")
    print("---")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Importar ferramentas no watsonx Orchestrate")
    parser.add_argument("--dry-run", action="store_true", help="Simula sem fazer alterações")
    parser.add_argument(
        "--force-update", action="store_true", help="Atualiza ferramentas existentes"
    )
    args = parser.parse_args()
    main(dry_run=args.dry_run, force_update=args.force_update)
