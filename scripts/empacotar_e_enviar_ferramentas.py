"""
scripts/empacotar_e_enviar_ferramentas.py
Empacota as 3 ferramentas em ZIPs corretos e faz o upload para o Orchestrate.

Cada ZIP contém:
  bundle-format          — versão do formato (2.0.0)
  requirements.txt       — dependências
  ferramentas/<nome>.py  — código da ferramenta
  fraude/__init__.py
  fraude/contratos.py
  fraude/repositorio.py
  dados/mock/operacional/clientes.json
  dados/mock/operacional/candidatas.json
  dados/mock/operacional/historico.json

Atualiza também o spec da ferramenta no Orchestrate para garantir
que agent_run_paramater="run" esteja registrado.
"""

from __future__ import annotations

import io
import json
import os
import sys
import zipfile
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
os.environ.setdefault("SENTINELA_ROOT", str(_ROOT))

from dotenv import load_dotenv

FERRAMENTAS = [
    {
        "nome": "consultar_contexto",
        "modulo": "ferramentas.contexto",
        "fn": "consultar_contexto",
        "arquivo_fonte": _ROOT / "ferramentas" / "contexto.py",
        "env_id": "WXO_TOOL_CONSULTAR_CONTEXTO_ID",
    },
    {
        "nome": "registrar_analise",
        "modulo": "ferramentas.analise",
        "fn": "registrar_analise",
        "arquivo_fonte": _ROOT / "ferramentas" / "analise.py",
        "env_id": "WXO_TOOL_REGISTRAR_ANALISE_ID",
    },
    {
        "nome": "executar_decisao",
        "modulo": "ferramentas.decisao",
        "fn": "executar_decisao",
        "arquivo_fonte": _ROOT / "ferramentas" / "decisao.py",
        "env_id": "WXO_TOOL_EXECUTAR_DECISAO_ID",
    },
]

REQUIREMENTS = """\
ibm-watsonx-orchestrate==2.19.1
pydantic>=2.0
"""

BUNDLE_FORMAT = "2.0.0\n"

DATA_FILES = [
    ("dados/mock/operacional/clientes.json", _ROOT / "dados/mock/operacional/clientes.json"),
    ("dados/mock/operacional/candidatas.json", _ROOT / "dados/mock/operacional/candidatas.json"),
    ("dados/mock/operacional/historico.json", _ROOT / "dados/mock/operacional/historico.json"),
]

FRAUDE_FILES = [
    ("fraude/__init__.py", _ROOT / "fraude" / "__init__.py"),
    ("fraude/contratos.py", _ROOT / "fraude" / "contratos.py"),
    ("fraude/repositorio.py", _ROOT / "fraude" / "repositorio.py"),
]


def _obter_cliente():
    from ibm_cloud_sdk_core.authenticators import IAMAuthenticator
    from ibm_watsonx_orchestrate.client.tools.tool_client import ToolClient

    url = os.environ["WXO_SERVICE_URL"].rstrip("/")
    key = os.environ["WXO_API_KEY"]
    auth = IAMAuthenticator(apikey=key)
    return ToolClient(base_url=url, authenticator=auth)


def _construir_zip(ferramenta: dict) -> bytes:
    """Constrói o ZIP da ferramenta em memória."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # bundle-format
        zf.writestr("bundle-format", BUNDLE_FORMAT)

        # requirements.txt
        zf.writestr("requirements.txt", REQUIREMENTS)

        # Código da ferramenta — no caminho correto para o binding
        modulo_path = ferramenta["modulo"].replace(".", "/") + ".py"
        # ex: ferramentas/contexto.py → ferramentas/contexto.py
        fonte = ferramenta["arquivo_fonte"].read_text(encoding="utf-8")
        zf.writestr(modulo_path, fonte)

        # __init__.py do pacote ferramentas (necessário para import)
        init_ferr = _ROOT / "ferramentas" / "__init__.py"
        if init_ferr.exists():
            zf.writestr("ferramentas/__init__.py", init_ferr.read_text(encoding="utf-8"))
        else:
            zf.writestr("ferramentas/__init__.py", "")

        # Módulos fraude
        for dest, src in FRAUDE_FILES:
            zf.writestr(dest, src.read_text(encoding="utf-8"))

        # Dados operacionais
        for dest, src in DATA_FILES:
            zf.writestr(dest, src.read_bytes())

    return buf.getvalue()


def _obter_spec_atual(ferramenta: dict) -> dict:
    """Obtém o spec atual da ferramenta pelo módulo Python."""
    import importlib

    nome_modulo, fn_nome = ferramenta["modulo"], ferramenta["fn"]
    mod = importlib.import_module(nome_modulo)
    fn = getattr(mod, fn_nome)
    return fn.__tool_spec__.model_dump(exclude_unset=True, exclude_none=True, mode="json")


def main() -> None:
    load_dotenv(_ROOT / ".env", override=False)
    url = os.environ.get("WXO_SERVICE_URL", "")
    key = os.environ.get("WXO_API_KEY", "")
    if not url or not key:
        print("ERRO: WXO_SERVICE_URL e WXO_API_KEY devem estar no .env")
        sys.exit(1)

    print(f"\n{'=' * 60}")
    print("  Empacotar e enviar ferramentas — Sentinela")
    print(f"{'=' * 60}\n")

    client = _obter_cliente()

    for ferr in FERRAMENTAS:
        nome = ferr["nome"]
        tool_id = os.environ.get(ferr["env_id"], "")
        if not tool_id:
            print(f"[{nome}] ERRO: {ferr['env_id']} não encontrado no .env")
            continue

        print(f"[{nome}]  id={tool_id}")

        # 1. Atualiza o spec (garante agent_run_paramater="run")
        print("  Atualizando spec no Orchestrate...")
        try:
            spec = _obter_spec_atual(ferr)
            client.update(tool_id, spec)
            print("  Spec atualizado.")
        except Exception as exc:
            print(f"  AVISO ao atualizar spec: {exc}")

        # 2. Constrói o ZIP
        print("  Construindo ZIP...")
        zip_bytes = _construir_zip(ferr)
        print(f"  ZIP pronto: {len(zip_bytes):,} bytes")

        # 3. Faz o upload
        print("  Fazendo upload...")
        tmp = _ROOT / f"_toolkit_{nome}.zip"
        try:
            tmp.write_bytes(zip_bytes)
            resp = client.upload_tools_artifact(tool_id, str(tmp))
            print(f"  Upload OK: {resp}")
        except Exception as exc:
            print(f"  ERRO no upload: {exc}")
        finally:
            if tmp.exists():
                tmp.unlink()

        print()

    print("Concluído.\n")


if __name__ == "__main__":
    main()
