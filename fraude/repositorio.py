"""
fraude/repositorio.py — Acesso aos dados sintéticos operacionais.

Carrega apenas os dados da pasta operacional (sem gabarito).
Nunca expõe dados de avaliação.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {}

_BASE = Path(__file__).parent.parent / "dados" / "mock" / "operacional"


def _carregar(nome: str) -> Any:
    with _LOCK:
        if nome not in _CACHE:
            caminho = _BASE / nome
            if not caminho.exists():
                raise FileNotFoundError(
                    f"Arquivo de dados não encontrado: {caminho}. "
                    "Execute scripts/gerar_dados.py primeiro."
                )
            with caminho.open(encoding="utf-8") as f:
                _CACHE[nome] = json.load(f)
        return _CACHE[nome]


def _invalidar_cache() -> None:
    """Usado nos testes para forçar recarga."""
    with _LOCK:
        _CACHE.clear()


# ---------------------------------------------------------------------------
# Clientes
# ---------------------------------------------------------------------------


def listar_clientes() -> list[dict]:
    return _carregar("clientes.json")


def _cliente_id(c: dict) -> str:
    """Retorna o ID do cliente nos dois formatos (Sicredi customer_id ou Sentinela id)."""
    return c.get("customer_id") or c.get("id", "")


def buscar_cliente(cliente_id: str) -> dict | None:
    for c in listar_clientes():
        if _cliente_id(c) == cliente_id:
            return c
    return None


def buscar_clientes_texto(
    texto: str,
    pagina: int = 1,
    por_pagina: int = 5,
) -> dict:
    """
    Busca clientes por nome, cidade, estado ou código — sem acento, sem distinção de caixa.
    Suporta campos Sicredi (customer_name, customer_city, customer_region, customer_id).
    Retorna dict com 'itens', 'total', 'pagina', 'por_pagina'.
    """
    import unicodedata

    def normalizar(s: str) -> str:
        return unicodedata.normalize("NFD", s.lower()).encode("ascii", "ignore").decode("ascii")

    termos = [normalizar(t) for t in texto.split() if t.strip()]
    todos = listar_clientes()

    if not termos:
        filtrados = todos
    else:
        filtrados = []
        for c in todos:
            campos = " ".join(
                [
                    c.get("customer_name") or c.get("nome", ""),
                    c.get("customer_city") or c.get("cidade", ""),
                    c.get("customer_region") or c.get("estado", ""),
                    c.get("customer_id") or c.get("id", ""),
                ]
            )
            campos_norm = normalizar(campos)
            if all(t in campos_norm for t in termos):
                filtrados.append(c)

    total = len(filtrados)
    inicio = (pagina - 1) * por_pagina
    fim = inicio + por_pagina
    return {
        "itens": filtrados[inicio:fim],
        "total": total,
        "pagina": pagina,
        "por_pagina": por_pagina,
        "total_paginas": max(1, (total + por_pagina - 1) // por_pagina),
    }


# ---------------------------------------------------------------------------
# Transações candidatas (operacional — sem gabarito)
# ---------------------------------------------------------------------------


def listar_candidatas_cliente(cliente_id: str) -> list[dict]:
    """Retorna as compras candidatas do cliente, ordenadas da mais recente à mais antiga.
    Suporta tanto cliente_id (Sentinela) quanto customer_id (Sicredi).
    """
    candidatas: list[dict] = _carregar("candidatas.json")
    cliente_cands = [
        c
        for c in candidatas
        if c.get("cliente_id") == cliente_id or c.get("customer_id") == cliente_id
    ]
    # Ordena pela data mais recente — suporta event_occurred_at (Sicredi) e data_hora (Sentinela)
    return sorted(
        cliente_cands,
        key=lambda x: x.get("event_occurred_at") or x.get("data_hora", ""),
        reverse=True,
    )


def buscar_transacao(transacao_id: str) -> dict | None:
    candidatas: list[dict] = _carregar("candidatas.json")
    for t in candidatas:
        if t["id"] == transacao_id or t.get("event_external_id") == transacao_id:
            return t
    return None


def buscar_historico(cliente_id: str) -> list[dict]:
    """Retorna o histórico de compras aprovadas do cliente."""
    historico: list[dict] = _carregar("historico.json")
    return [
        h
        for h in historico
        if h.get("cliente_id") == cliente_id or h.get("customer_id") == cliente_id
    ]


# ---------------------------------------------------------------------------
# Referências (apenas para os agentes via ferramenta, não para o frontend)
# ---------------------------------------------------------------------------


def carregar_referencias() -> list[dict]:
    ref_path = Path(__file__).parent.parent / "dados" / "mock" / "referencias"
    resultado = []
    for arq in sorted(ref_path.glob("*.json")):
        with arq.open(encoding="utf-8") as f:
            resultado.extend(json.load(f))
    return resultado


# ---------------------------------------------------------------------------
# Contexto operacional completo para a ferramenta consultar_contexto
# ---------------------------------------------------------------------------


def montar_contexto_operacional(transacao_id: str) -> dict:
    """
    Monta o payload completo entregue à ferramenta consultar_contexto.
    Inclui: transação, cliente, cartão, histórico resumido e eventos recentes.
    Campos no formato Sicredi quando disponíveis.
    NÃO inclui gabarito, rótulo, cenário ou referências de avaliação.
    """
    transacao = buscar_transacao(transacao_id)
    if transacao is None:
        raise ValueError(f"Transação '{transacao_id}' não encontrada.")

    cliente_id = transacao.get("customer_id") or transacao.get("cliente_id", "")
    cliente = buscar_cliente(cliente_id) or {}
    historico = buscar_historico(cliente_id)
    historico_resumo = _resumir_historico(historico)

    # Cartão: prefere campos Sicredi, cai de volta para estrutura aninhada
    cartao = {
        "card_id": transacao.get("card_id") or transacao.get("cartao", {}).get("id"),
        "card_brand": transacao.get("card_brand") or transacao.get("cartao", {}).get("bandeira"),
        "card_last4": transacao.get("card_last4") or transacao.get("cartao", {}).get("final"),
        "card_type": transacao.get("card_type") or transacao.get("cartao", {}).get("tipo"),
        "card_credit_limit": transacao.get("card_credit_limit"),
        "card_presence_flag": transacao.get("card_presence_flag"),
    }

    return {
        "transacao": transacao,
        "cliente": {
            # Campos Sicredi (primários)
            "customer_id": cliente.get("customer_id") or cliente.get("id"),
            "customer_name": cliente.get("customer_name") or cliente.get("nome"),
            "customer_city": cliente.get("customer_city") or cliente.get("cidade"),
            "customer_region": cliente.get("customer_region") or cliente.get("estado"),
            "customer_country_code": cliente.get("customer_country_code") or cliente.get("pais"),
            "customer_risk_rating": cliente.get("customer_risk_rating"),
            "customer_segment": cliente.get("customer_segment"),
            # Perfil de gasto (contexto operacional)
            "perfil_gasto": cliente.get("perfil_gasto"),
            # Aliases Sentinela para compatibilidade
            "id": cliente.get("customer_id") or cliente.get("id"),
            "nome": cliente.get("customer_name") or cliente.get("nome"),
        },
        "cartao": cartao,
        "historico_resumo": historico_resumo,
        "eventos_recentes": transacao.get("eventos_recentes", []),
    }


def _resumir_historico(historico: list[dict]) -> dict:
    if not historico:
        return {
            "total_transacoes": 0,
            "valor_medio": None,
            "ticket_medio_historico": None,
            "categorias_frequentes": [],
            "estabelecimentos_frequentes": [],
            "localizacoes_frequentes": [],
            "canais_frequentes": [],
            "tipos_transacao": [],
        }

    # Suporta tanto amount (Sicredi) quanto valor (Sentinela)
    valores = [
        h.get("amount") or h.get("valor", 0)
        for h in historico
        if (h.get("amount") or h.get("valor")) is not None
    ]
    valor_medio = round(sum(valores) / len(valores), 2) if valores else None

    from collections import Counter

    # Categoria: suporta campo direto ou via estabelecimento aninhado
    categorias = Counter(
        h.get("categoria") or h.get("estabelecimento", {}).get("categoria", "")
        for h in historico
        if h.get("categoria") or h.get("estabelecimento", {}).get("categoria")
    )
    # Estabelecimento: acceptor_name (Sicredi) ou estabelecimento.nome (Sentinela)
    estabelecimentos = Counter(
        h.get("acceptor_name") or h.get("estabelecimento", {}).get("nome", "")
        for h in historico
        if h.get("acceptor_name") or h.get("estabelecimento", {}).get("nome")
    )
    # Localização: acceptor_city / acceptor_country_code (Sicredi) ou cidade/pais (Sentinela)
    localizacoes = Counter(
        f"{h.get('acceptor_city') or h.get('cidade', '')}, "
        f"{h.get('acceptor_country_code') or h.get('pais', '')}"
        for h in historico
    )
    canais = Counter(h.get("canal", "") for h in historico if h.get("canal"))
    tipos = Counter(h.get("tipo_transacao", "") for h in historico if h.get("tipo_transacao"))

    return {
        "total_transacoes": len(historico),
        "valor_medio": valor_medio,
        "ticket_medio_historico": valor_medio,
        "categorias_frequentes": [k for k, _ in categorias.most_common(5)],
        "estabelecimentos_frequentes": [k for k, _ in estabelecimentos.most_common(5)],
        "localizacoes_frequentes": [k for k, _ in localizacoes.most_common(5)],
        "canais_frequentes": [k for k, _ in canais.most_common(3)],
        "tipos_transacao": [k for k, _ in tipos.most_common(3)],
    }
