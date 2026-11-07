"""Executa o código e os dados de cada ZIP em um processo isolado."""

import io
import json
import subprocess
import sys
import zipfile

import pytest

from scripts.empacotar_e_enviar_ferramentas import FERRAMENTAS, _construir_zip
from scripts.sincronizar_orchestrate import comparar_pacotes


@pytest.mark.parametrize("ferramenta", FERRAMENTAS, ids=lambda f: f["nome"])
def test_pacote_executa_funcao_real_com_todos_ids(ferramenta, tmp_path):
    pacote = _construir_zip(ferramenta)
    with zipfile.ZipFile(io.BytesIO(pacote)) as zf:
        nomes = zf.namelist()
        assert not any("avaliacao" in n or ".env" in n or "gabarito" in n for n in nomes)
        zf.extractall(tmp_path)
    # Só o decorador do SDK externo é substituído. As funções, schemas,
    # repositório, arquivos e JSON Pointers executados são os do ZIP real.
    codigo = r"""
import sys, types, json, importlib
from pathlib import Path
sys.path.insert(0, sys.argv[1])
for nome in ["ibm_watsonx_orchestrate", "ibm_watsonx_orchestrate.agent_builder",
             "ibm_watsonx_orchestrate.agent_builder.tools",
             "ibm_watsonx_orchestrate.agent_builder.tools.python_tool"]:
    sys.modules[nome] = types.ModuleType(nome)
sys.modules[nome].tool = lambda **kwargs: lambda fn: fn
modulo = importlib.import_module(sys.argv[2])
funcao = getattr(modulo, sys.argv[3])
txs = json.loads(Path("dados/mock/operacional/candidatas.json").read_bytes())
assert len(txs) == 45
for tx in txs:
    payload = {"versao": 2, "transacao_id": tx["id"],
        "evidencias": [{"caminho": "/valor", "descricao": "Valor informado."}],
        "justificativa": "Verificacao isolada do pacote."}
    if sys.argv[3] == "consultar_contexto":
        retorno = funcao(tx["id"])
        assert "roteiro_cliente" not in json.dumps(retorno)
        assert retorno["transacao"]["id"] == tx["id"]
    else:
        if sys.argv[3] == "registrar_analise":
            payload.update(risco="baixo", acao="autorizar", informacoes_ausentes=[])
        else:
            payload.update(desfecho="autorizar", pergunta=None)
        retorno = funcao(tx["id"], json.dumps(payload))
        payload["evidencias"][0]["caminho"] = "/inventado"
        assert funcao(tx["id"], json.dumps(payload))["status"] == "erro"
        assert funcao(tx["id"], "[]")["status"] == "erro"
        assert funcao("TX-INEXISTENTE", json.dumps(payload))["status"] == "erro"
    assert retorno["status"] == "ok"
if sys.argv[3] == "consultar_contexto":
    assert funcao("TX-INEXISTENTE")["status"] == "erro"
else:
    # Sem o arquivo, nenhuma ferramenta pode produzir comprovante de sucesso.
    Path("dados/mock/operacional/candidatas.json").unlink()
    from fraude.repositorio import _invalidar_cache
    _invalidar_cache()
    assert funcao(tx["id"], json.dumps(payload))["status"] == "erro"
print("45 IDs e limites do contrato verificados")
"""
    resultado = subprocess.run(
        [sys.executable, "-I", "-c", codigo, str(tmp_path), ferramenta["modulo"], ferramenta["fn"]],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert resultado.returncode == 0, resultado.stderr


def test_verificador_detecta_dados_e_codigo_divergentes():
    pacote = _construir_zip(FERRAMENTAS[0])
    assert comparar_pacotes(pacote, pacote)["arquivos_iguais"]
    destino = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(pacote)) as origem, zipfile.ZipFile(destino, "w") as alterado:
        for nome in origem.namelist():
            conteudo = origem.read(nome)
            if nome.endswith("candidatas.json"):
                dados = json.loads(conteudo)
                dados[0]["id"] = "TX-ALTERADO"
                conteudo = json.dumps(dados).encode()
            alterado.writestr(nome, conteudo)
    diferenca = comparar_pacotes(pacote, destino.getvalue())
    assert not diferenca["arquivos_iguais"] and not diferenca["ids_iguais"]
    assert diferenca["ids_compartilhados"] == 44
