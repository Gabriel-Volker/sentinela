"""
web/rotas.py — Handlers das rotas HTTP do Sentinela.

Valida entradas, coordena casos de uso e serializa respostas.
Não contém regras de negócio — delega ao serviço e ao coordenador.
"""

from __future__ import annotations

import json
import logging
import traceback
from typing import Any

from fraude.coordenador import Coordenador, ErroCriterioComprovante, ErroIntegracao
from fraude.repositorio import buscar_clientes_texto, listar_candidatas_cliente
from fraude.servico import (
    ErroConcorrencia,
    ErroConflito,
    ErroEntradaInvalida,
    ErroNaoEncontrado,
    ServicoCasos,
)

logger = logging.getLogger(__name__)

# Limite de tamanho do corpo da requisição (bytes)
_LIMITE_CORPO = 8_192

# Caracteres permitidos no ID de transação
import re

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _id_valido(s: str) -> bool:
    return bool(_ID_RE.match(s))


def _ler_corpo(rfile, content_length: int) -> bytes:
    if content_length > _LIMITE_CORPO:
        raise ErroEntradaInvalida(f"Corpo excede {_LIMITE_CORPO} bytes.")
    return rfile.read(content_length)


def _json_ok(data: Any) -> tuple[int, dict]:
    return 200, {"status": "ok", "dados": data}


def _json_erro(codigo: int, mensagem: str) -> tuple[int, dict]:
    return codigo, {"status": "erro", "mensagem": mensagem}


# ---------------------------------------------------------------------------
# Roteador central
# ---------------------------------------------------------------------------


class Roteador:
    def __init__(self, servico: ServicoCasos, coordenador: Coordenador) -> None:
        self._svc = servico
        self._coord = coordenador

    def despachar(
        self,
        metodo: str,
        caminho: str,
        query: str,
        rfile,
        content_length: int,
        headers: dict,
    ) -> tuple[int, dict]:
        try:
            return self._roteador(metodo, caminho, query, rfile, content_length, headers)
        except ErroEntradaInvalida as exc:
            return _json_erro(400, str(exc))
        except ErroNaoEncontrado as exc:
            return _json_erro(404, str(exc))
        except ErroConflito as exc:
            return _json_erro(409, str(exc))
        except ErroConcorrencia as exc:
            return _json_erro(409, str(exc))
        except ErroIntegracao as exc:
            return _json_erro(503, f"Serviço IBM indisponível: {exc}")
        except ErroCriterioComprovante as exc:
            return _json_erro(502, f"Comprovante inválido: {exc}")
        except Exception as exc:
            logger.error("Erro interno: %s\n%s", exc, traceback.format_exc())
            return _json_erro(500, "Erro interno do servidor.")

    def _roteador(
        self,
        metodo: str,
        caminho: str,
        query: str,
        rfile,
        content_length: int,
        headers: dict,
    ) -> tuple[int, dict]:
        # Normaliza caminho
        partes = [p for p in caminho.strip("/").split("/") if p]

        # GET /health
        if metodo == "GET" and partes == ["health"]:
            return self._health()

        # GET /catalogo?q=...&pagina=...
        if metodo == "GET" and partes == ["catalogo"]:
            return self._catalogo(query)

        # GET /contexto/_candidatas/{cliente_id}
        if (
            metodo == "GET"
            and len(partes) == 3
            and partes[0] == "contexto"
            and partes[1] == "_candidatas"
        ):
            return self._candidatas_cliente(partes[2])

        # GET /contexto/{id}
        if metodo == "GET" and len(partes) == 2 and partes[0] == "contexto":
            return self._contexto(partes[1])

        # GET /casos  (listar sessão)
        if metodo == "GET" and partes == ["casos"]:
            return self._listar_casos()

        # GET /casos/{id}
        if metodo == "GET" and len(partes) == 2 and partes[0] == "casos":
            return self._obter_caso(partes[1])

        # POST /casos/{id}/analisar
        if (
            metodo == "POST"
            and len(partes) == 3
            and partes[0] == "casos"
            and partes[2] == "analisar"
        ):
            corpo = self._ler_json(rfile, content_length)
            return self._analisar(partes[1], corpo)

        # POST /casos/{id}/responder
        if (
            metodo == "POST"
            and len(partes) == 3
            and partes[0] == "casos"
            and partes[2] == "responder"
        ):
            corpo = self._ler_json(rfile, content_length)
            return self._responder(partes[1], corpo)

        # POST /casos/{id}/simular
        if (
            metodo == "POST"
            and len(partes) == 3
            and partes[0] == "casos"
            and partes[2] == "simular"
        ):
            return self._simular(partes[1])

        # POST /casos/{id}/consultar
        if (
            metodo == "POST"
            and len(partes) == 3
            and partes[0] == "casos"
            and partes[2] == "consultar"
        ):
            return self._consultar_run(partes[1])

        # GET /exportar
        if metodo == "GET" and partes == ["exportar"]:
            return self._exportar()

        return _json_erro(404, f"Rota não encontrada: {metodo} /{'/'.join(partes)}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ler_json(self, rfile, content_length: int) -> dict:
        raw = _ler_corpo(rfile, content_length)
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ErroEntradaInvalida(f"JSON inválido: {exc}") from exc

    def _validar_tx_id(self, tx_id: str) -> None:
        if not _id_valido(tx_id):
            raise ErroEntradaInvalida(f"ID de transação inválido: {tx_id!r}")

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _health(self) -> tuple[int, dict]:
        return 200, {
            "status": "ok",
            "servico": "Sentinela",
            "ambiente": "local_demo",
            "aviso": (
                "Processo local. Dados sintéticos. "
                "Este health check NÃO confirma conectividade IBM."
            ),
        }

    def _catalogo(self, query: str) -> tuple[int, dict]:
        params = _parse_query(query)
        q = params.get("q", "").strip()
        pagina = int(params.get("pagina", "1") or "1")
        pagina = max(1, pagina)

        if not q:
            # Retorna primeiros clientes sem filtro
            from fraude.repositorio import listar_clientes

            todos = listar_clientes()
            por_pag = 5
            inicio = (pagina - 1) * por_pag
            clientes_pag = todos[inicio : inicio + por_pag]
            total = len(todos)
            return _json_ok(
                {
                    "clientes": clientes_pag,
                    "total": total,
                    "pagina": pagina,
                    "por_pagina": por_pag,
                    "total_paginas": max(1, (total + por_pag - 1) // por_pag),
                }
            )

        resultado = buscar_clientes_texto(q, pagina=pagina, por_pagina=5)
        return _json_ok(
            {
                "clientes": resultado["itens"],
                "total": resultado["total"],
                "pagina": resultado["pagina"],
                "por_pagina": resultado["por_pagina"],
                "total_paginas": resultado["total_paginas"],
            }
        )

    def _candidatas_cliente(self, cliente_id: str) -> tuple[int, dict]:
        if not _id_valido(cliente_id):
            raise ErroEntradaInvalida(f"ID de cliente inválido: {cliente_id!r}")
        candidatas = listar_candidatas_cliente(cliente_id)
        # Remove roteiro do cliente antes de servir
        candidatas_seguras = []
        for c in candidatas:
            cs = {k: v for k, v in c.items() if k != "roteiro_cliente"}
            candidatas_seguras.append(cs)
        return _json_ok({"candidatas": candidatas_seguras})

    def _contexto(self, tx_id: str) -> tuple[int, dict]:
        self._validar_tx_id(tx_id)
        ctx = self._svc.obter_contexto_transacao(tx_id)
        # Remove roteiro do cliente — não exposto ao frontend
        ctx_seguro = {k: v for k, v in ctx.items() if k != "roteiro_cliente"}
        if "transacao" in ctx_seguro:
            ctx_seguro["transacao"] = {
                k: v for k, v in ctx_seguro["transacao"].items() if k != "roteiro_cliente"
            }
        return _json_ok(ctx_seguro)

    def _listar_casos(self) -> tuple[int, dict]:
        return _json_ok(self._svc.listar_casos())

    def _obter_caso(self, tx_id: str) -> tuple[int, dict]:
        self._validar_tx_id(tx_id)
        try:
            caso = self._svc.obter_caso(tx_id)
        except ErroNaoEncontrado:
            # Caso não existe — retorna estado inicial para o frontend
            return _json_ok({"transacao_id": tx_id, "estado": "novo"})
        return _json_ok(caso.to_dict())

    def _analisar(self, tx_id: str, corpo: dict) -> tuple[int, dict]:
        self._validar_tx_id(tx_id)
        modo = corpo.get("modo", "")
        if modo not in ("manual", "roteiro"):
            raise ErroEntradaInvalida("modo deve ser 'manual' ou 'roteiro'.")
        caso = self._coord.executar_analise(tx_id, modo)
        return _json_ok(caso.to_dict())

    def _responder(self, tx_id: str, corpo: dict) -> tuple[int, dict]:
        self._validar_tx_id(tx_id)
        sem_resposta = corpo.get("sem_resposta", False)
        texto = corpo.get("texto")

        if not isinstance(sem_resposta, bool):
            raise ErroEntradaInvalida("sem_resposta deve ser booleano.")
        if sem_resposta and texto is not None:
            raise ErroEntradaInvalida("texto deve ser null quando sem_resposta=true.")
        if not sem_resposta and (not texto or not isinstance(texto, str)):
            raise ErroEntradaInvalida("texto é obrigatório quando sem_resposta=false.")
        if texto and len(texto) > 2000:
            raise ErroEntradaInvalida("texto excede 2000 caracteres.")

        # Registra resposta do cliente
        caso = self._svc.registrar_resposta_cliente(tx_id, texto, sem_resposta)

        # Dispara validação
        caso = self._coord.executar_validacao(tx_id, texto)
        return _json_ok(caso.to_dict())

    def _simular(self, tx_id: str) -> tuple[int, dict]:
        """Avança o roteiro sintético em um turno."""
        self._validar_tx_id(tx_id)
        caso = self._svc.obter_caso(tx_id)
        if caso.modo != "roteiro":
            raise ErroEntradaInvalida("Simulação disponível apenas no modo 'roteiro'.")
        fala = self._svc.proximo_turno_roteiro(tx_id)
        return _json_ok({"fala": fala, "fim_roteiro": fala is None})

    def _consultar_run(self, tx_id: str) -> tuple[int, dict]:
        self._validar_tx_id(tx_id)
        caso = self._coord.consultar_run_pendente(tx_id)
        return _json_ok(caso.to_dict())

    def _exportar(self) -> tuple[int, dict]:
        return _json_ok(self._svc.exportar_sessao())


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------


def _parse_query(query: str) -> dict[str, str]:
    """Parse simples de query string."""
    params: dict[str, str] = {}
    if not query:
        return params
    for par in query.split("&"):
        if "=" in par:
            k, _, v = par.partition("=")
            from urllib.parse import unquote_plus

            params[k] = unquote_plus(v)
    return params
