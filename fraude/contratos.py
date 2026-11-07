"""
fraude/contratos.py — Fonte única dos schemas do Sentinela.

Usado tanto na validação local quanto na configuração remota das ferramentas.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class NivelRisco(str, Enum):
    BAIXO = "baixo"
    MEDIO = "medio"
    ALTO = "alto"


class AcaoAnalise(str, Enum):
    AUTORIZAR = "autorizar"
    VALIDAR = "validar"
    BLOQUEAR = "bloquear"


class DesfechoValidacao(str, Enum):
    AGUARDAR_CLIENTE = "aguardar_cliente"
    AUTORIZAR = "autorizar"
    BLOQUEAR = "bloquear"
    PENDENTE = "pendente"


# ---------------------------------------------------------------------------
# Correspondências obrigatórias
# ---------------------------------------------------------------------------

RISCO_ACAO: dict[NivelRisco, AcaoAnalise] = {
    NivelRisco.BAIXO: AcaoAnalise.AUTORIZAR,
    NivelRisco.MEDIO: AcaoAnalise.VALIDAR,
    NivelRisco.ALTO: AcaoAnalise.BLOQUEAR,
}

# Desfechos que encerram a validação (não podem ter "pergunta" preenchida)
DESFECHOS_FINAIS: set[DesfechoValidacao] = {
    DesfechoValidacao.AUTORIZAR,
    DesfechoValidacao.BLOQUEAR,
    DesfechoValidacao.PENDENTE,
}

# ---------------------------------------------------------------------------
# Submodelos
# ---------------------------------------------------------------------------


class Evidencia(BaseModel):
    """Evidência produzida pelo agente — somente caminho e descrição."""

    caminho: str = Field(
        description="JSON Pointer (RFC 6901) apontando para um campo escalar na transação."
    )
    descricao: str = Field(description="Descrição da evidência para o analista.")
    # valor é SEMPRE resolvido pelo backend, nunca copiado pelo agente
    valor: Any | None = Field(default=None, exclude=True)

    @field_validator("caminho")
    @classmethod
    def caminho_deve_ser_pointer(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError(f"caminho deve começar com '/' (JSON Pointer). Recebido: {v!r}")
        return v

    @field_validator("descricao")
    @classmethod
    def descricao_nao_vazia(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("descricao não pode ser vazio.")
        return v


# ---------------------------------------------------------------------------
# Contrato de Análise (versao: 2)
# ---------------------------------------------------------------------------


class RespostaAnalise(BaseModel):
    """Contrato de saída do agente de análise."""

    versao: int = Field(description="Versão obrigatória do contrato: 2.")
    transacao_id: str
    risco: NivelRisco
    acao: AcaoAnalise
    evidencias: list[Evidencia] = Field(min_length=1)
    informacoes_ausentes: list[str] = Field(default_factory=list)
    justificativa: str

    @field_validator("versao")
    @classmethod
    def versao_deve_ser_2(cls, v: int) -> int:
        if v != 2:
            raise ValueError(f"versao deve ser 2, recebido {v}.")
        return v

    @model_validator(mode="after")
    def acao_compativel_com_risco(self) -> RespostaAnalise:
        esperada = RISCO_ACAO.get(self.risco)
        if esperada and self.acao != esperada:
            raise ValueError(
                f"Ação '{self.acao}' incompatível com risco '{self.risco}'. Esperado: '{esperada}'."
            )
        return self

    @field_validator("justificativa")
    @classmethod
    def justificativa_nao_vazia(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("justificativa não pode ser vazia.")
        return v

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Contrato de Validação (versao: 2)
# ---------------------------------------------------------------------------


class RespostaValidacao(BaseModel):
    """Contrato de saída do agente de validação."""

    versao: int = Field(description="Versão obrigatória do contrato: 2.")
    transacao_id: str
    desfecho: DesfechoValidacao
    pergunta: str | None = Field(default=None)
    evidencias: list[Evidencia] = Field(min_length=1)
    justificativa: str

    @field_validator("versao")
    @classmethod
    def versao_deve_ser_2(cls, v: int) -> int:
        if v != 2:
            raise ValueError(f"versao deve ser 2, recebido {v}.")
        return v

    @model_validator(mode="after")
    def desfecho_final_sem_pergunta(self) -> RespostaValidacao:
        if self.desfecho in DESFECHOS_FINAIS and self.pergunta is not None:
            raise ValueError(
                f"Desfecho final '{self.desfecho}' não pode ter 'pergunta' preenchida."
            )
        if self.desfecho == DesfechoValidacao.AGUARDAR_CLIENTE and not self.pergunta:
            raise ValueError("Desfecho 'aguardar_cliente' requer 'pergunta' preenchida.")
        return self

    @field_validator("justificativa")
    @classmethod
    def justificativa_nao_vazia(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("justificativa não pode ser vazia.")
        return v

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Comprovante verificável
# ---------------------------------------------------------------------------

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class Comprovante(BaseModel):
    """Comprovante gerado pelo backend após cada operação confirmada."""

    operacao_id: str
    transacao_id: str
    fase: str  # "analise" | "validacao"
    hash_conteudo: str  # SHA-256 determinístico do payload original
    timestamp_iso: str

    @field_validator("operacao_id", "transacao_id")
    @classmethod
    def id_valido(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError(f"ID inválido: {v!r}")
        return v


def calcular_hash(payload: dict | str) -> str:
    """SHA-256 determinístico de um payload JSON."""
    if isinstance(payload, dict):
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    else:
        raw = payload
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Helpers de parsing
# ---------------------------------------------------------------------------


def parsear_analise(raw: str | dict) -> RespostaAnalise:
    """Parse e validação da resposta do agente de análise."""
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON inválido na resposta de análise: {exc}") from exc
    else:
        data = raw
    return RespostaAnalise.model_validate(data)


def parsear_validacao(raw: str | dict) -> RespostaValidacao:
    """Parse e validação da resposta do agente de validação."""
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON inválido na resposta de validação: {exc}") from exc
    else:
        data = raw
    return RespostaValidacao.model_validate(data)


def resolver_pointer(transacao: dict, caminho: str) -> Any:
    """
    Resolve um JSON Pointer (RFC 6901) na transação.

    Retorna o valor encontrado.
    Levanta ValueError se o caminho apontar para um objeto/lista ou não existir.
    """
    if caminho == "":
        raise ValueError("JSON Pointer vazio não é permitido como evidência.")
    if not caminho.startswith("/"):
        raise ValueError(f"caminho deve começar com '/' (JSON Pointer). Recebido: {caminho!r}")
    segmentos = caminho.lstrip("/").split("/")
    atual: Any = transacao
    for seg in segmentos:
        # unescape RFC 6901
        seg = seg.replace("~1", "/").replace("~0", "~")
        if isinstance(atual, dict):
            if seg not in atual:
                raise ValueError(f"Caminho '{caminho}' não encontrado na transação.")
            atual = atual[seg]
        elif isinstance(atual, list):
            try:
                atual = atual[int(seg)]
            except (IndexError, ValueError) as exc:
                raise ValueError(f"Índice inválido '{seg}' no caminho '{caminho}'.") from exc
        else:
            raise ValueError(f"Caminho '{caminho}' aponta para um nó não navegável.")
    if isinstance(atual, (dict, list)):
        raise ValueError(
            f"Evidência '{caminho}' aponta para um objeto/lista completo, "
            "não para um campo escalar."
        )
    return atual


def validar_evidencias(evidencias: list[Evidencia], transacao: dict) -> list[Evidencia]:
    """
    Resolve e anexa os valores originais nas evidências.
    Levanta ValueError se algum caminho for inválido.
    """
    resultado = []
    for ev in evidencias:
        valor = resolver_pointer(transacao, ev.caminho)
        ev_com_valor = ev.model_copy(update={"valor": valor})
        resultado.append(ev_com_valor)
    return resultado
