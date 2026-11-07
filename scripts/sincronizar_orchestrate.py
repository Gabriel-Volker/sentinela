"""Compara ou atualiza os cinco recursos existentes definidos no .env.

Sem --aplicar, apenas consulta. Antes de alterar, salva os metadados e os
ZIPs anteriores em .local/orchestrate/. Nunca cria instâncias ou novos IDs.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from ibm.iam import token_atual
from scripts.empacotar_e_enviar_ferramentas import FERRAMENTAS, _construir_zip

AGENTES = [("analise", "WXO_AGENT_ANALISE_ID"), ("validacao", "WXO_AGENT_VALIDACAO_ID")]


class SemRedirecionamento(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Cliente:
    def __init__(self):
        self.base = os.environ["WXO_SERVICE_URL"].rstrip("/")
        url = urllib.parse.urlsplit(self.base)
        if (
            url.scheme != "https"
            or not (url.hostname or "").endswith(".ibm.com")
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path.endswith("/v1")
        ):
            raise ValueError("WXO_SERVICE_URL deve ser a URL HTTPS da instância IBM, sem /v1.")
        self.opener = urllib.request.build_opener(SemRedirecionamento())

    def request(self, caminho, metodo="GET", dados=None, content_type="application/json"):
        headers = {"Authorization": f"Bearer {token_atual()}", "Accept": "application/json"}
        if dados is not None:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(
            self.base + "/v1/orchestrate/" + caminho, headers=headers, data=dados, method=metodo
        )
        try:
            with self.opener.open(req, timeout=60) as resposta:
                conteudo = resposta.read(30_000_001)
                if len(conteudo) > 30_000_000:
                    raise ValueError("Resposta excede o limite de tamanho.")
                return conteudo
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"IBM retornou HTTP {exc.code} em {metodo} {caminho}.") from None

    def json(self, caminho, metodo="GET", dados=None):
        corpo = None if dados is None else json.dumps(dados).encode()
        retorno = self.request(caminho, metodo, corpo)
        return json.loads(retorno) if retorno else {}


def arquivos_zip(conteudo):
    with zipfile.ZipFile(io.BytesIO(conteudo)) as pacote:
        if sum(item.file_size for item in pacote.infolist()) > 30_000_000:
            raise ValueError("Pacote descompactado excede o limite de tamanho.")
        return {nome: pacote.read(nome) for nome in pacote.namelist() if not nome.endswith("/")}


def comparar_pacotes(local, remoto):
    esperado, atual = arquivos_zip(local), arquivos_zip(remoto)
    iguais = set(esperado) == set(atual)
    for nome, conteudo in esperado.items():
        outro = atual.get(nome)
        if outro is None:
            iguais = False
        elif nome.endswith(".json"):
            iguais &= json.loads(conteudo) == json.loads(outro)
        else:
            iguais &= conteudo.decode("utf-8-sig").replace("\r\n", "\n") == outro.decode(
                "utf-8-sig"
            ).replace("\r\n", "\n")
    caminho = "dados/mock/operacional/candidatas.json"
    ids = {tx["id"] for tx in json.loads(esperado[caminho])}
    ids_remotos = {tx["id"] for tx in json.loads(atual.get(caminho, b"[]"))}
    return {
        "arquivos_iguais": bool(iguais),
        "ids_locais": len(ids),
        "ids_compartilhados": len(ids & ids_remotos),
        "ids_iguais": ids == ids_remotos,
    }


def carregar_agente(fase):
    import yaml

    spec = yaml.safe_load((ROOT / "agentes" / f"{fase}.yaml").read_text(encoding="utf-8"))
    mapa = {f["nome"]: str(uuid.UUID(os.environ[f["env_id"]])) for f in FERRAMENTAS}
    return {
        "name": spec["name"],
        "instructions": spec["instructions"],
        "context_variables": spec["context_variables"],
        "llm": spec["llm"],
        "tools": [mapa[nome] for nome in spec["tools"]],
    }


def divergencias_agente(esperado, remoto):
    return [
        campo
        for campo, valor in esperado.items()
        if (valor.strip() if isinstance(valor, str) else sorted(valor))
        != (
            remoto.get(campo, "").strip()
            if isinstance(valor, str)
            else sorted(remoto.get(campo, []))
        )
    ]


def sincronizar(cliente, aplicar=False):
    ferramentas, agentes = [], []
    # Valida todos os IDs, nomes e pacotes antes da primeira escrita remota.
    for ferr in FERRAMENTAS:
        ident = str(uuid.UUID(os.environ[ferr["env_id"]]))
        meta = cliente.json(f"tools/{ident}")
        if meta.get("name") != ferr["nome"]:
            raise ValueError(f"ID de {ferr['nome']} aponta para outra ferramenta.")
        ferramentas.append(
            (ferr, ident, meta, _construir_zip(ferr), cliente.request(f"tools/{ident}/download"))
        )
    for fase, variavel in AGENTES:
        ident = str(uuid.UUID(os.environ[variavel]))
        esperado, remoto = carregar_agente(fase), cliente.json(f"agents/{ident}")
        if esperado["name"] != remoto.get("name"):
            raise ValueError(f"ID do agente {fase} aponta para outro recurso.")
        agentes.append((fase, ident, esperado, remoto))

    if aplicar:
        backup = (
            ROOT
            / ".local"
            / "orchestrate"
            / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        )
        backup.mkdir(parents=True)
        for ferr, _ident, meta, _pacote, anterior in ferramentas:
            (backup / f"{ferr['nome']}.zip").write_bytes(anterior)
            (backup / f"{ferr['nome']}.json").write_text(json.dumps(meta), encoding="utf-8")
        for fase, _ident, _esperado, remoto in agentes:
            (backup / f"agente_{fase}.json").write_text(json.dumps(remoto), encoding="utf-8")
        for _ferr, ident, _meta, pacote, anterior in ferramentas:
            if comparar_pacotes(pacote, anterior)["arquivos_iguais"]:
                continue
            limite = "sentinela-" + uuid.uuid4().hex
            corpo = (
                (
                    f'--{limite}\r\nContent-Disposition: form-data; name="file"; filename="{ident}.zip"\r\n'
                    "Content-Type: application/zip\r\n\r\n"
                ).encode()
                + pacote
                + f"\r\n--{limite}--\r\n".encode()
            )
            cliente.request(
                f"tools/{ident}/upload", "POST", corpo, f"multipart/form-data; boundary={limite}"
            )
        for _fase, ident, esperado, remoto in agentes:
            if divergencias_agente(esperado, remoto):
                cliente.json(f"agents/{ident}", "PATCH", esperado)

    relatorio = {"ferramentas": {}, "agentes": {}}
    for ferr, ident, _meta, pacote, anterior in ferramentas:
        remoto = cliente.request(f"tools/{ident}/download") if aplicar else anterior
        relatorio["ferramentas"][ferr["nome"]] = comparar_pacotes(pacote, remoto)
    for fase, ident, esperado, remoto in agentes:
        remoto = cliente.json(f"agents/{ident}") if aplicar else remoto
        relatorio["agentes"][fase] = {"divergencias": divergencias_agente(esperado, remoto)}
    return relatorio


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aplicar", action="store_true")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env", override=False)
    relatorio = sincronizar(Cliente(), args.aplicar)
    print(json.dumps(relatorio, indent=2, ensure_ascii=False))
    divergente = any(not f["arquivos_iguais"] for f in relatorio["ferramentas"].values()) or any(
        a["divergencias"] for a in relatorio["agentes"].values()
    )
    return 1 if divergente else 0


if __name__ == "__main__":
    raise SystemExit(main())
