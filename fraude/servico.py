"""
fraude/servico.py — Coordenação dos casos de uso.

O serviço mantém os casos em memória durante a sessão.
Reiniciar o servidor perde o estado. Exportações não restauram sessões.
"""

from __future__ import annotations

import threading
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from fraude.contratos import (
    Comprovante,
    DesfechoValidacao,
    NivelRisco,
    RespostaAnalise,
    RespostaValidacao,
    calcular_hash,
)
from fraude.estados import EstadoCaso, transicao_valida
from fraude.repositorio import buscar_transacao, montar_contexto_operacional

# Limite de perguntas do agente de validação por caso
MAX_PERGUNTAS = 3


class ErroConflito(Exception):
    """Operação incompatível com o estado atual do caso."""


class ErroNaoEncontrado(Exception):
    """Caso ou recurso não existe na sessão."""


class ErroEntradaInvalida(Exception):
    """Payload ou parâmetro inválido."""


class ErroConcorrencia(Exception):
    """Operação simultânea detectada no mesmo caso."""


# ---------------------------------------------------------------------------
# Modelo de caso em memória
# ---------------------------------------------------------------------------


class Caso:
    def __init__(self, transacao_id: str) -> None:
        self.id: str = transacao_id
        self.transacao_id: str = transacao_id
        self.estado: EstadoCaso = EstadoCaso.NOVO
        self.modo: str | None = None  # "manual" | "roteiro"
        self.analise: RespostaAnalise | None = None
        self.validacoes: list[RespostaValidacao] = []
        self.conversa: list[dict] = []  # histórico de turnos do chat
        self.comprovantes: list[Comprovante] = []
        self.decisao_aplicada: bool = False  # protege contra duplicação
        self.run_analise: dict | None = None  # {thread_id, run_id, agente}
        self.run_validacao: dict | None = None
        self.falha_integracao: dict | None = None
        self.revisao = 0
        self.criado_em: str = _agora()
        self.atualizado_em: str = _agora()
        self._lock = threading.Lock()
        self.roteiro_posicao: int = 0  # índice da próxima fala no roteiro

    def to_dict(self, incluir_run_ids: bool = False) -> dict:
        with self._lock:
            return self._snapshot(incluir_run_ids)

    def _snapshot(self, incluir_run_ids: bool = False) -> dict:
        d: dict[str, Any] = {
            "id": self.id,
            "transacao_id": self.transacao_id,
            "estado": self.estado.value,
            "modo": self.modo,
            "criado_em": self.criado_em,
            "atualizado_em": self.atualizado_em,
            "revisao": self.revisao,
        }
        if self.analise:
            d["analise"] = self.analise.model_dump(exclude={"versao"})
        if self.validacoes:
            d["ultima_validacao"] = self.validacoes[-1].model_dump(exclude={"versao"})
        if self.conversa:
            d["conversa"] = deepcopy(self.conversa)
        if self.falha_integracao:
            d["falha_integracao"] = dict(self.falha_integracao)
        if self.comprovantes:
            d["comprovantes"] = [c.model_dump() for c in self.comprovantes]
        if incluir_run_ids:
            if self.run_analise:
                d["run_analise"] = dict(self.run_analise)
            if self.run_validacao:
                d["run_validacao"] = dict(self.run_validacao)
        return d


# ---------------------------------------------------------------------------
# Serviço (singleton de sessão)
# ---------------------------------------------------------------------------


class ServicoCasos:
    def __init__(self, ibm_adapter=None) -> None:
        self._casos: dict[str, Caso] = {}
        self._lock = threading.Lock()
        self._ibm = ibm_adapter  # pode ser None em modo offline / testes

    def _obter_ou_criar(self, transacao_id: str) -> Caso:
        with self._lock:
            if transacao_id not in self._casos:
                # Valida que a transação existe antes de criar o caso
                tx = buscar_transacao(transacao_id)
                if tx is None:
                    raise ErroNaoEncontrado(
                        f"Transação '{transacao_id}' não encontrada no catálogo."
                    )
                self._casos[transacao_id] = Caso(transacao_id)
            return self._casos[transacao_id]

    def obter_caso(self, transacao_id: str) -> Caso:
        caso = self._casos.get(transacao_id)
        if caso is None:
            raise ErroNaoEncontrado(f"Caso para '{transacao_id}' não existe nesta sessão.")
        return caso

    # ------------------------------------------------------------------
    # Catálogo (delegado ao repositório)
    # ------------------------------------------------------------------

    def obter_contexto_transacao(self, transacao_id: str) -> dict:
        try:
            return montar_contexto_operacional(transacao_id)
        except ValueError as exc:
            raise ErroNaoEncontrado(str(exc)) from exc

    # ------------------------------------------------------------------
    # Análise
    # ------------------------------------------------------------------

    def iniciar_analise(self, transacao_id: str, modo: str, operacao_id: str = "") -> Caso:
        if modo not in ("manual", "roteiro"):
            raise ErroEntradaInvalida("modo deve ser 'manual' ou 'roteiro'.")
        caso = self._obter_ou_criar(transacao_id)
        with caso._lock:
            if caso.estado != EstadoCaso.NOVO:
                raise ErroConflito(
                    f"Análise não pode ser iniciada no estado '{caso.estado.value}'."
                )
            caso.modo = modo
            caso.estado = EstadoCaso.ANALISANDO
            if operacao_id:
                caso.run_analise = {"operacao_id": operacao_id, "run_id": "", "agente": "analise"}
            caso.atualizado_em = _agora()
            caso.revisao += 1
        return caso

    def registrar_resultado_analise(
        self,
        transacao_id: str,
        analise: RespostaAnalise,
        run_info: dict,
        comprovante: Comprovante,
    ) -> Caso | None:
        caso = self._obter_ou_criar(transacao_id)
        with caso._lock:
            if not _resultado_vigente(caso.run_analise, run_info):
                return None
            if caso.estado not in (EstadoCaso.ANALISANDO, EstadoCaso.PENDENTE_EXECUCAO):
                raise ErroConflito(
                    f"Resultado de análise rejeitado no estado '{caso.estado.value}'."
                )
            _verificar_transacao(analise.transacao_id, transacao_id)
            caso.analise = analise
            caso.run_analise = {**run_info, "processado": True}
            caso.comprovantes.append(comprovante)
            # transição de estado conforme risco
            novo_estado = _risco_para_estado(analise.risco)
            caso.estado = novo_estado
            caso.atualizado_em = _agora()
            caso.revisao += 1
        return caso

    # ------------------------------------------------------------------
    # Resposta do cliente (chat)
    # ------------------------------------------------------------------

    def registrar_resposta_cliente(
        self,
        transacao_id: str,
        texto: str | None,
        sem_resposta: bool,
    ) -> Caso:
        caso = self.obter_caso(transacao_id)
        with caso._lock:
            if caso.estado != EstadoCaso.AGUARDANDO_CLIENTE:
                raise ErroConflito(
                    f"Resposta do cliente rejeitada no estado '{caso.estado.value}'."
                )
            if sem_resposta:
                turno = {"papel": "cliente", "texto": None, "sem_resposta": True, "ts": _agora()}
            else:
                if not texto or not texto.strip():
                    raise ErroEntradaInvalida("texto não pode ser vazio quando sem_resposta=false.")
                if len(texto) > 2000:
                    raise ErroEntradaInvalida("texto excede 2000 caracteres.")
                turno = {"papel": "cliente", "texto": texto, "sem_resposta": False, "ts": _agora()}
            caso.conversa.append(turno)
            caso.estado = EstadoCaso.VALIDANDO
            caso.atualizado_em = _agora()
            caso.revisao += 1
        return caso

    # ------------------------------------------------------------------
    # Validação
    # ------------------------------------------------------------------

    def registrar_resultado_validacao(
        self,
        transacao_id: str,
        validacao: RespostaValidacao,
        run_info: dict,
        comprovante: Comprovante,
    ) -> Caso | None:
        caso = self.obter_caso(transacao_id)
        with caso._lock:
            if not _resultado_vigente(caso.run_validacao, run_info):
                return None
            if caso.estado not in (EstadoCaso.VALIDANDO, EstadoCaso.PENDENTE_EXECUCAO):
                raise ErroConflito(
                    f"Resultado de validação rejeitado no estado '{caso.estado.value}'."
                )
            _verificar_transacao(validacao.transacao_id, transacao_id)

            # Verifica limite de perguntas
            n_perguntas = sum(
                1 for v in caso.validacoes if v.desfecho == DesfechoValidacao.AGUARDAR_CLIENTE
            )
            if (
                validacao.desfecho == DesfechoValidacao.AGUARDAR_CLIENTE
                and n_perguntas >= MAX_PERGUNTAS
            ):
                # Passou o limite: mantém pendente
                validacao = validacao.model_copy(
                    update={"desfecho": DesfechoValidacao.PENDENTE, "pergunta": None}
                )

            # Registra pergunta do agente na conversa
            if validacao.pergunta:
                caso.conversa.append(
                    {
                        "papel": "agente",
                        "texto": validacao.pergunta,
                        "ts": _agora(),
                    }
                )

            caso.validacoes.append(validacao)
            caso.run_validacao = {**run_info, "processado": True}
            caso.comprovantes.append(comprovante)

            novo_estado = _desfecho_para_estado(validacao.desfecho)
            caso.estado = novo_estado
            caso.atualizado_em = _agora()
            caso.revisao += 1
        return caso

    # ------------------------------------------------------------------
    # Decisão simulada (executar_decisao)
    # ------------------------------------------------------------------

    def aplicar_decisao(
        self,
        transacao_id: str,
        acao: str,
        comprovante: Comprovante,
    ) -> Caso:
        """Aplica a decisão final simulada. Idempotência garantida."""
        caso = self.obter_caso(transacao_id)
        with caso._lock:
            if caso.decisao_aplicada:
                raise ErroConflito("Decisão já foi aplicada para esta transação nesta sessão.")
            estado_destino = {
                "autorizar": EstadoCaso.AUTORIZADO,
                "bloquear": EstadoCaso.BLOQUEADO,
                "pendente": EstadoCaso.PENDENTE,
            }.get(acao)
            if estado_destino is None:
                raise ErroEntradaInvalida(f"Ação desconhecida: '{acao}'.")
            if not transicao_valida(caso.estado, estado_destino):
                raise ErroConflito(
                    f"Transição inválida: '{caso.estado.value}' → '{estado_destino.value}'."
                )
            caso.comprovantes.append(comprovante)
            caso.estado = estado_destino
            caso.decisao_aplicada = True
            caso.atualizado_em = _agora()
            caso.revisao += 1
        return caso

    # ------------------------------------------------------------------
    # Consulta de execução remota
    # ------------------------------------------------------------------

    def reservar_validacao(self, transacao_id: str, operacao_id: str) -> dict:
        caso = self.obter_caso(transacao_id)
        with caso._lock:
            if caso.estado != EstadoCaso.VALIDANDO:
                raise ErroConflito("Já existe uma validação em andamento ou encerrada.")
            anterior = caso.run_validacao or {}
            caso.run_validacao = {
                "operacao_id": operacao_id,
                "run_id": "",
                "thread_id": anterior.get("thread_id", ""),
                "agente": "validacao",
            }
            caso.estado = EstadoCaso.PENDENTE_EXECUCAO
            caso.atualizado_em = _agora()
            caso.revisao += 1
            return dict(caso.run_validacao)

    def registrar_falha_integracao(
        self, transacao_id: str, fase: str, run_info: dict, codigo: str
    ) -> Caso:
        """Encerra somente a operação correspondente, sem criar decisão ou comprovante."""
        caso = self.obter_caso(transacao_id)
        with caso._lock:
            atributo = "run_analise" if fase == "analise" else "run_validacao"
            if not _resultado_vigente(getattr(caso, atributo), run_info):
                return caso
            setattr(caso, atributo, {**run_info, "processado": True})
            caso.falha_integracao = {
                "fase": fase,
                "codigo": codigo,
                "mensagem": "A execução não produziu um resultado verificável. Encaminhe o caso para revisão humana.",
            }
            caso.estado = EstadoCaso.PENDENTE
            caso.atualizado_em = _agora()
            caso.revisao += 1
        return caso

    def registrar_run_pendente(
        self,
        transacao_id: str,
        run_info: dict,
        fase: str,
    ) -> bool:
        caso = self._obter_ou_criar(transacao_id)
        with caso._lock:
            atual = caso.run_analise if fase == "analise" else caso.run_validacao
            # A conclusão não pode ser sobrescrita pelo criador que voltou da rede depois do poll.
            if (
                atual
                and atual.get("operacao_id") == run_info.get("operacao_id")
                and atual.get("processado")
            ):
                return False
            if atual and atual.get("operacao_id") != run_info.get("operacao_id"):
                return False
            if fase == "analise":
                caso.run_analise = dict(run_info)
            else:
                caso.run_validacao = dict(run_info)
            caso.estado = EstadoCaso.PENDENTE_EXECUCAO
            caso.atualizado_em = _agora()
            caso.revisao += 1
            return True

    # ------------------------------------------------------------------
    # Simulador de cliente por turno
    # ------------------------------------------------------------------

    def proximo_turno_roteiro(self, transacao_id: str) -> str | None:
        """
        Retorna a próxima fala do roteiro sintético para o modo 'roteiro'.
        Retorna None quando o roteiro terminar.
        Apenas uma fala por chamada — o roteiro completo não é exposto.
        """
        from fraude.repositorio import buscar_transacao

        tx = buscar_transacao(transacao_id)
        if tx is None:
            return None
        roteiro: list[str] = tx.get("roteiro_cliente", [])
        caso = self._casos.get(transacao_id)
        if caso is None:
            return None
        with caso._lock:
            pos = caso.roteiro_posicao
            if pos >= len(roteiro):
                return None
            caso.roteiro_posicao += 1
        return roteiro[pos]

    # ------------------------------------------------------------------
    # Exportação da sessão
    # ------------------------------------------------------------------

    def exportar_sessao(self) -> dict:
        """
        Exporta todos os casos da sessão atual.
        Não inclui credenciais, gabarito ou dados de avaliação.
        """
        casos_exportados = []
        with self._lock:
            for caso in self._casos.values():
                with caso._lock:
                    casos_exportados.append(caso._snapshot())
        return {
            "versao_exportacao": "1",
            "exportado_em": _agora(),
            "aviso": (
                "Exportação de sessão local. Dados sintéticos. "
                "Operações simuladas. Reiniciar o servidor perde o estado."
            ),
            "casos": casos_exportados,
        }

    def listar_casos(self) -> list[dict]:
        with self._lock:
            return [c.to_dict() for c in self._casos.values()]


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resultado_vigente(atual: dict | None, recebido: dict) -> bool:
    if not atual:
        return True  # serviço também pode ser usado sem adaptador remoto
    return (
        not atual.get("processado")
        and atual.get("operacao_id", "") == recebido.get("operacao_id", "")
        and atual.get("run_id", "") == recebido.get("run_id", "")
    )


def _risco_para_estado(risco: NivelRisco) -> EstadoCaso:
    return {
        NivelRisco.BAIXO: EstadoCaso.AUTORIZADO,
        NivelRisco.ALTO: EstadoCaso.BLOQUEADO,
        NivelRisco.MEDIO: EstadoCaso.VALIDANDO,
    }[risco]


def _desfecho_para_estado(desfecho: DesfechoValidacao) -> EstadoCaso:
    return {
        DesfechoValidacao.AGUARDAR_CLIENTE: EstadoCaso.AGUARDANDO_CLIENTE,
        DesfechoValidacao.AUTORIZAR: EstadoCaso.AUTORIZADO,
        DesfechoValidacao.BLOQUEAR: EstadoCaso.BLOQUEADO,
        DesfechoValidacao.PENDENTE: EstadoCaso.PENDENTE,
    }[desfecho]


def _verificar_transacao(transacao_id_recebido: str, transacao_id_esperado: str) -> None:
    if transacao_id_recebido != transacao_id_esperado:
        raise ErroEntradaInvalida(
            f"Transação divergente: esperado '{transacao_id_esperado}', "
            f"recebido '{transacao_id_recebido}'."
        )


def gerar_comprovante(operacao_id: str, transacao_id: str, fase: str, payload: dict) -> Comprovante:
    return Comprovante(
        operacao_id=operacao_id,
        transacao_id=transacao_id,
        fase=fase,
        hash_conteudo=calcular_hash(payload),
        timestamp_iso=_agora(),
    )
