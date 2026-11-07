"""
fraude/estados.py — Estados do caso e máquina de transições.
"""

from __future__ import annotations

from enum import Enum


class EstadoCaso(str, Enum):
    """Estados possíveis de um caso de análise."""

    NOVO = "novo"
    ANALISANDO = "analisando"
    VALIDANDO = "validando"
    PENDENTE_EXECUCAO = "pendente_execucao"
    AGUARDANDO_CLIENTE = "aguardando_cliente"
    AUTORIZADO = "autorizado"
    BLOQUEADO = "bloqueado"
    PENDENTE = "pendente"


# Transições permitidas: estado_atual -> conjunto de próximos estados válidos
TRANSICOES: dict[EstadoCaso, set[EstadoCaso]] = {
    EstadoCaso.NOVO: {EstadoCaso.ANALISANDO},
    EstadoCaso.ANALISANDO: {
        EstadoCaso.AUTORIZADO,  # risco baixo
        EstadoCaso.BLOQUEADO,  # risco alto
        EstadoCaso.VALIDANDO,  # risco médio
        EstadoCaso.PENDENTE_EXECUCAO,  # timeout local — run ainda ativo
        EstadoCaso.PENDENTE,  # falha permanente
    },
    EstadoCaso.VALIDANDO: {
        EstadoCaso.AGUARDANDO_CLIENTE,
        EstadoCaso.AUTORIZADO,
        EstadoCaso.BLOQUEADO,
        EstadoCaso.PENDENTE_EXECUCAO,
        EstadoCaso.PENDENTE,
    },
    EstadoCaso.PENDENTE_EXECUCAO: {
        EstadoCaso.AUTORIZADO,
        EstadoCaso.BLOQUEADO,
        EstadoCaso.VALIDANDO,
        EstadoCaso.AGUARDANDO_CLIENTE,
        EstadoCaso.PENDENTE,
    },
    EstadoCaso.AGUARDANDO_CLIENTE: {
        EstadoCaso.VALIDANDO,  # nova fala recebida
        EstadoCaso.AUTORIZADO,
        EstadoCaso.BLOQUEADO,
        EstadoCaso.PENDENTE,
    },
    # estados terminais — sem transições
    EstadoCaso.AUTORIZADO: set(),
    EstadoCaso.BLOQUEADO: set(),
    EstadoCaso.PENDENTE: set(),
}

# Estados terminais (sem transições)
ESTADOS_TERMINAIS: set[EstadoCaso] = {
    EstadoCaso.AUTORIZADO,
    EstadoCaso.BLOQUEADO,
    EstadoCaso.PENDENTE,
}


def transicao_valida(atual: EstadoCaso, proximo: EstadoCaso) -> bool:
    return proximo in TRANSICOES.get(atual, set())
