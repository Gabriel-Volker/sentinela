"""
fraude/coordenador.py — Coordena chamadas IBM e atualiza o serviço de casos.

Responsabilidades:
  - Constrói o contexto (context_variables) para cada run.
  - Chama o adaptador IBM (real ou fake).
  - Extrai e valida a resposta do agente.
  - Atualiza o serviço de casos com resultado verificado.
  - Verifica comprovantes reais das ferramentas no histórico da execução.

O backend é responsável pelo estado. Os agentes não calculam risco alternativo.

Separação de etapas:
  - executar_analise retorna assim que a análise for concluída.
  - Para risco médio, o run de validação é iniciado em segundo plano (threading.Thread).
  - O frontend faz poll via POST /casos/{id}/consultar para obter o resultado.
  - Isso permite mostrar o parecer inicial sem esperar a primeira pergunta.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid

from fraude.contratos import (
    DesfechoValidacao,
    calcular_hash,
    parsear_analise,
    parsear_validacao,
    validar_evidencias,
)
from fraude.estados import EstadoCaso
from fraude.repositorio import buscar_transacao
from fraude.servico import (
    Caso,
    ErroEntradaInvalida,
    ErroNaoEncontrado,
    ServicoCasos,
    gerar_comprovante,
)

logger = logging.getLogger(__name__)

# Orçamento local de espera (segundos). Não cancela o run remoto ao esgotar.
_POLL_TIMEOUT_S = float(os.environ.get("SENTINELA_POLL_TIMEOUT_S", "90"))

# Intervalo inicial de poll. Cresce linearmente até o máximo.
# 1.5s inicial: análise remota leva 18-21s; detectamos na 12.ª–14.ª iteração da soma
# (1.5+2+2.5+3+3+3...) sem disparar muitas requisições. Validação leva 4-8s;
# detectamos na 2.ª–4.ª consulta (1.5s, 3.5s, 6.5s), economizando 1-3s vs intervalo de 3s.
_POLL_INTERVALO_INICIAL_S = float(os.environ.get("SENTINELA_POLL_INTERVALO_S", "1.5"))

# Intervalo máximo entre consultas.
# 3s é suficiente para análise longa (18-21s): ~6-7 consultas no total.
# Reduzido de 10s para não atrasar detecção quando o run completar próximo ao intervalo.
_POLL_INTERVALO_MAX_S = float(os.environ.get("SENTINELA_POLL_MAX_S", "3.0"))


class ErroIntegracao(Exception):
    """Falha ao comunicar com IBM Orchestrate."""


class ErroCriterioComprovante(Exception):
    """Comprovante ausente, inválido ou divergente."""


# ---------------------------------------------------------------------------
# Coordenador
# ---------------------------------------------------------------------------


class Coordenador:
    def __init__(
        self,
        servico: ServicoCasos,
        ibm,
        agente_analise_id: str | None = None,
        agente_validacao_id: str | None = None,
    ) -> None:
        """
        ibm: adaptador IBM (orchestrate.py ou AdaptadorFake).
        Deve ter: criar_run(), aguardar_run(), consultar_run(), extrair_json_resposta()
        agente_analise_id / agente_validacao_id: IDs dos agentes (lidos do env se None).
        """
        self._svc = servico
        self._ibm = ibm
        self._agente_analise_id = agente_analise_id
        self._agente_validacao_id = agente_validacao_id
        # Mapa run_id → timestamp monotônico da última consulta IBM feita pelo worker
        # (preenchido por _aguardar_run_com_timeout via ts_worker).
        # Se o worker consultou recentemente, consultar_run_pendente devolve estado local.
        # Cada instância tem seu próprio mapa — evita contaminação entre testes.
        self._ultima_consulta_worker: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Análise
    # ------------------------------------------------------------------

    def executar_analise(self, transacao_id: str, modo: str) -> Caso:
        """
        Inicia a análise via agente de análise.
        Bloqueia até conclusão ou timeout local.

        Para risco médio, inicia o agente de validação em segundo plano e
        retorna o caso imediatamente após a análise. O frontend faz poll
        via POST /casos/{id}/consultar para obter a primeira pergunta.
        """
        operacao_id = f"OP-{uuid.uuid4().hex[:12].upper()}"
        caso = self._svc.iniciar_analise(transacao_id, modo, operacao_id)
        agente_id = self._agente_analise_id or os.environ.get("WXO_AGENT_ANALISE_ID", "")

        if not agente_id:
            logger.warning("WXO_AGENT_ANALISE_ID não configurado — modo offline.")
            return caso

        ctx_vars = _montar_ctx_analise(operacao_id, transacao_id)
        mensagem = _prompt_analise(transacao_id)

        t_inicio = time.monotonic()
        logger.info("[diag] analise run_create start tx=%s", transacao_id)
        try:
            run_info = self._ibm.criar_run(
                agente_id=agente_id,
                mensagem=mensagem,
                thread_id=None,
                context_variables=ctx_vars,
            )
        except Exception as exc:
            logger.error("Falha ao criar run de análise: %s", exc)
            info = self._svc.obter_caso(transacao_id).to_dict(True)["run_analise"]
            self._svc.registrar_falha_integracao(transacao_id, "analise", info, "criacao_falhou")
            raise ErroIntegracao(f"Falha ao criar run: {exc}") from exc

        t_run_criado = time.monotonic()
        logger.info(
            "[diag] analise run_created tx=%s run_id=%s t=%.2fs",
            transacao_id,
            run_info.get("run_id", "?"),
            t_run_criado - t_inicio,
        )

        run_id = run_info.get("run_id", "")
        thread_id = run_info.get("thread_id", "")
        self._svc.registrar_run_pendente(
            transacao_id,
            {
                "run_id": run_id,
                "thread_id": thread_id,
                "agente": "analise",
                "operacao_id": operacao_id,
            },
            "analise",
        )

        try:
            run_status = _aguardar_run_com_timeout(
                self._ibm, run_id, _POLL_TIMEOUT_S, self._ultima_consulta_worker
            )
        except Exception as exc:
            logger.warning("Timeout/erro ao aguardar run de análise %s: %s", run_id, exc)
            return caso

        t_run_concluido = time.monotonic()
        logger.info(
            "[diag] analise run_done tx=%s run_id=%s status=%s t_total=%.2fs",
            transacao_id,
            run_id,
            run_status.get("status"),
            t_run_concluido - t_inicio,
        )

        # Se o run ainda não completou, retorna pendente_execucao sem processar
        estado_run = (run_status.get("status") or "").lower()
        if estado_run not in {"completed", "failed", "cancelled", "error"}:
            logger.info(
                "Run %s ainda em andamento (%s). Caso em pendente_execucao.", run_id, estado_run
            )
            return caso

        return self._processar_resultado_analise(
            transacao_id, operacao_id, run_id, thread_id, run_status, t_inicio
        )

    def _processar_resultado(
        self, fase, transacao_id, operacao_id, run_id, thread_id, run_status, *args
    ):
        info = {
            "operacao_id": operacao_id,
            "run_id": run_id,
            "thread_id": thread_id,
            "agente": fase,
        }
        status = (run_status.get("status") or "").lower()
        if run_status.get("id", run_id) != run_id:
            return self._svc.registrar_falha_integracao(transacao_id, fase, info, "run_divergente")
        if status != "completed":
            if status in {"failed", "cancelled", "error"}:
                return self._svc.registrar_falha_integracao(transacao_id, fase, info, status)
            return self._svc.obter_caso(transacao_id)
        try:
            aplicar = getattr(self, "_aplicar_resultado_" + fase)
            return aplicar(transacao_id, operacao_id, run_id, thread_id, run_status, *args)
        except (
            ErroCriterioComprovante,
            ErroEntradaInvalida,
            ValueError,
            TypeError,
            AttributeError,
        ) as exc:
            logger.warning(
                "Resultado remoto rejeitado tx=%s fase=%s tipo=%s",
                transacao_id,
                fase,
                type(exc).__name__,
            )
            return self._svc.registrar_falha_integracao(
                transacao_id, fase, info, "resultado_invalido"
            )

    def _processar_resultado_analise(
        self, transacao_id, operacao_id, run_id, thread_id, run_status, t_inicio=None
    ):
        return self._processar_resultado(
            "analise", transacao_id, operacao_id, run_id, thread_id, run_status, t_inicio
        )

    def _processar_resultado_validacao(
        self,
        transacao_id,
        operacao_id,
        run_id,
        thread_id,
        run_status,
        respostas_cliente,
        t_inicio=None,
    ):
        return self._processar_resultado(
            "validacao",
            transacao_id,
            operacao_id,
            run_id,
            thread_id,
            run_status,
            respostas_cliente,
            t_inicio,
        )

    def _aplicar_resultado_analise(
        self,
        transacao_id: str,
        operacao_id: str,
        run_id: str,
        thread_id: str,
        run_status: dict,
        t_inicio: float | None = None,
    ) -> Caso:
        dados = self._ibm.extrair_json_resposta(run_status)
        if not isinstance(dados, dict):
            raise ErroCriterioComprovante("Resposta sem JSON estruturado.")

        # Normaliza evidências (suporte ao formato de string do Frontier)
        if isinstance(dados.get("evidencias"), list):
            dados = {**dados, "evidencias": _normalizar_evidencias(dados["evidencias"])}

        # Valida contrato
        try:
            analise = parsear_analise(dados)
        except Exception as exc:
            raise ErroCriterioComprovante(f"Contrato de análise inválido: {exc}") from exc

        # Valida evidências contra a transação (sem heurística de correção)
        tx = buscar_transacao(transacao_id)
        if tx is None:
            raise ErroNaoEncontrado(f"Transação '{transacao_id}' não encontrada.")
        try:
            analise = analise.model_copy(
                update={"evidencias": validar_evidencias(analise.evidencias, tx)}
            )
        except ValueError as exc:
            raise ErroCriterioComprovante(f"Evidência inválida: {exc}") from exc

        # Verifica comprovante da ferramenta registrar_analise no histórico
        _verificar_comprovante_analise(run_status, operacao_id, transacao_id, analise)

        comprovante = gerar_comprovante(operacao_id, transacao_id, "analise", analise.model_dump())
        run_info = {
            "run_id": run_id,
            "thread_id": thread_id,
            "agente": "analise",
            "operacao_id": operacao_id,
        }

        caso = self._svc.registrar_resultado_analise(transacao_id, analise, run_info, comprovante)

        if caso is None:
            return self._svc.obter_caso(transacao_id)

        if t_inicio is not None:
            logger.info(
                "[diag] analise concluida tx=%s risco=%s t_ate_parecer=%.2fs",
                transacao_id,
                analise.risco.value,
                time.monotonic() - t_inicio,
            )

        # Para risco médio: inicia o agente de validação em segundo plano.
        # O frontend obtém o parecer imediatamente; a primeira pergunta chega
        # via poll POST /casos/{id}/consultar quando o agente de validação concluir.
        from fraude.contratos import NivelRisco as _NR
        from fraude.estados import EstadoCaso as _EC

        if analise.risco == _NR.MEDIO and caso.estado == _EC.VALIDANDO:
            return self._iniciar_validacao_background(transacao_id, caso)

        return caso

    def _iniciar_validacao_background(self, transacao_id: str, caso_analise) -> Caso:
        operacao_id = f"OP-{uuid.uuid4().hex[:12].upper()}"
        self._svc.reservar_validacao(transacao_id, operacao_id)

        def tarefa():
            try:
                self.executar_validacao(transacao_id, None, operacao_id=operacao_id)
            except ErroIntegracao:
                # executar_validacao já registrou o diagnóstico da operação correspondente.
                logger.warning("Criação de validação falhou tx=%s", transacao_id)
            except Exception:
                logger.exception("Erro na validação tx=%s", transacao_id)
                info = self._svc.obter_caso(transacao_id).to_dict(True).get("run_validacao", {})
                if info.get("operacao_id") == operacao_id:
                    self._svc.registrar_falha_integracao(
                        transacao_id, "validacao", info, "erro_local"
                    )

        threading.Thread(target=tarefa, name=f"validacao-{transacao_id}", daemon=True).start()
        return self._svc.obter_caso(transacao_id)

    # ------------------------------------------------------------------
    # Validação
    # ------------------------------------------------------------------

    def executar_validacao(
        self, transacao_id: str, resposta_cliente: str | None, *, operacao_id: str | None = None
    ) -> Caso:
        """
        Executa o agente de validação após receber resposta do cliente.
        Apenas casos com risco médio chegam aqui.
        """
        caso = self._svc.obter_caso(transacao_id)
        if operacao_id is None:
            operacao_id = f"OP-{uuid.uuid4().hex[:12].upper()}"
            self._svc.reservar_validacao(transacao_id, operacao_id)
        snapshot = caso.to_dict(True)
        reserva = snapshot["run_validacao"]
        if reserva.get("operacao_id") != operacao_id or reserva.get("processado"):
            return caso
        agente_id = self._agente_validacao_id or os.environ.get("WXO_AGENT_VALIDACAO_ID", "")
        if not agente_id:
            return self._svc.registrar_falha_integracao(
                transacao_id, "validacao", reserva, "agente_ausente"
            )
        # Condensed analysis for context_variables: versao, transacao_id, risco, acao,
        # evidencias (caminhos sem descrição longa) e justificativa truncada.
        # A ferramenta executar_decisao não precisa do JSON completo da análise —
        # ela recebe o JSON de validacao. O agente precisa do risco e da ação para decidir.
        analise_json = _condensar_analise(caso.analise) if caso.analise else "{}"
        conversa = snapshot.get("conversa", [])
        respostas = [t for t in conversa if t.get("papel") == "cliente"]
        respostas_json = json.dumps(respostas, ensure_ascii=False)
        thread_id_anterior = reserva.get("thread_id") or None
        thread_existente = bool(thread_id_anterior)

        ctx_vars = _montar_ctx_validacao(
            operacao_id,
            transacao_id,
            analise_json,
            respostas_json,
            thread_existente=thread_existente,
        )
        mensagem = _prompt_validacao(transacao_id, conversa)

        t_inicio = time.monotonic()
        logger.info(
            "[diag] validacao run_create start tx=%s thread_existente=%s",
            transacao_id,
            thread_existente,
        )
        try:
            run_info = self._ibm.criar_run(
                agente_id=agente_id,
                mensagem=mensagem,
                thread_id=thread_id_anterior,
                context_variables=ctx_vars,
            )
        except Exception as exc:
            logger.error("Falha ao criar run de validação: %s", exc)
            info = self._svc.obter_caso(transacao_id).to_dict(True)["run_validacao"]
            self._svc.registrar_falha_integracao(transacao_id, "validacao", info, "criacao_falhou")
            raise ErroIntegracao(f"Falha ao criar run: {exc}") from exc

        t_run_criado = time.monotonic()
        run_id = run_info.get("run_id", "")
        thread_id = run_info.get("thread_id", "")
        logger.info(
            "[diag] validacao run_created tx=%s run_id=%s t=%.2fs",
            transacao_id,
            run_id,
            t_run_criado - t_inicio,
        )
        self._svc.registrar_run_pendente(
            transacao_id,
            {
                "run_id": run_id,
                "thread_id": thread_id,
                "agente": "validacao",
                "operacao_id": operacao_id,
            },
            "validacao",
        )

        try:
            run_status = _aguardar_run_com_timeout(
                self._ibm, run_id, _POLL_TIMEOUT_S, self._ultima_consulta_worker
            )
        except Exception as exc:
            logger.warning("Timeout/erro ao aguardar run de validação %s: %s", run_id, exc)
            return caso

        t_run_concluido = time.monotonic()
        logger.info(
            "[diag] validacao run_done tx=%s run_id=%s status=%s t_total=%.2fs",
            transacao_id,
            run_id,
            run_status.get("status"),
            t_run_concluido - t_inicio,
        )

        estado_run = (run_status.get("status") or "").lower()
        if estado_run not in {"completed", "failed", "cancelled", "error"}:
            logger.info("Run de validação %s ainda em andamento (%s).", run_id, estado_run)
            return caso

        return self._processar_resultado_validacao(
            transacao_id, operacao_id, run_id, thread_id, run_status, respostas, t_inicio
        )

    def _aplicar_resultado_validacao(
        self,
        transacao_id: str,
        operacao_id: str,
        run_id: str,
        thread_id: str,
        run_status: dict,
        respostas_cliente: list[dict],
        t_inicio: float | None = None,
    ) -> Caso:
        dados = self._ibm.extrair_json_resposta(run_status)
        if not isinstance(dados, dict):
            raise ErroCriterioComprovante("Resposta sem JSON estruturado.")

        # Normaliza evidências (suporte ao formato de string do Frontier)
        if isinstance(dados.get("evidencias"), list):
            dados = {**dados, "evidencias": _normalizar_evidencias(dados["evidencias"])}

        try:
            validacao = parsear_validacao(dados)
        except Exception as exc:
            raise ErroCriterioComprovante(f"Contrato de validação inválido: {exc}") from exc

        tx = buscar_transacao(transacao_id)
        if tx is None:
            raise ErroNaoEncontrado(f"Transação '{transacao_id}' não encontrada.")
        try:
            validacao = validacao.model_copy(
                update={"evidencias": validar_evidencias(validacao.evidencias, tx)}
            )
        except ValueError as exc:
            raise ErroCriterioComprovante(f"Evidência inválida: {exc}") from exc

        # Comprovante deve corresponder à saída original, antes das restrições locais.
        if validacao.desfecho != DesfechoValidacao.AGUARDAR_CLIENTE:
            _verificar_comprovante_decisao(run_status, operacao_id, transacao_id, validacao)

        # Verifica ausência de resposta → pendente (não autoriza)
        ultima_resposta = respostas_cliente[-1] if respostas_cliente else None
        if ultima_resposta and ultima_resposta.get("sem_resposta"):
            if validacao.desfecho == DesfechoValidacao.AUTORIZAR:
                logger.warning(
                    "Agente tentou autorizar após ausência de resposta — revertendo para pendente."
                )
                validacao = validacao.model_copy(
                    update={
                        "desfecho": DesfechoValidacao.PENDENTE,
                        "pergunta": None,
                        "justificativa": (
                            "O cliente não respondeu. Sem confirmação, a compra não pode "
                            "ser liberada automaticamente e segue para revisão humana."
                        ),
                    }
                )

        comprovante = gerar_comprovante(
            operacao_id, transacao_id, "validacao", validacao.model_dump()
        )
        run_info = {
            "run_id": run_id,
            "thread_id": thread_id,
            "agente": "validacao",
            "operacao_id": operacao_id,
        }

        caso = self._svc.registrar_resultado_validacao(
            transacao_id, validacao, run_info, comprovante
        )

        if caso is None:
            return self._svc.obter_caso(transacao_id)

        if t_inicio is not None:
            logger.info(
                "[diag] validacao concluida tx=%s desfecho=%s t_ate_pergunta=%.2fs",
                transacao_id,
                validacao.desfecho.value,
                time.monotonic() - t_inicio,
            )

        return caso

    # ------------------------------------------------------------------
    # Consulta de run pendente
    # ------------------------------------------------------------------

    def consultar_run_pendente(self, transacao_id: str) -> Caso:
        """
        Consulta uma run que ainda não completou localmente.

        Devolve o estado local imediatamente se o caso já não está pendente.
        Evita consultas IBM concorrentes com o worker de background: se o worker
        atualizou o run_info recentemente (dentro de _POLL_INTERVALO_INICIAL_S),
        devolve o snapshot local sem chamar IBM novamente.

        Preserva run_id, thread_id, operacao_id e fase da operação original.
        Não gera novo operacao_id para processar o mesmo resultado — usa o
        operacao_id registrado em run_info["operacao_id"] quando disponível.
        """
        caso = self._svc.obter_caso(transacao_id)

        if caso.estado not in (EstadoCaso.PENDENTE_EXECUCAO, EstadoCaso.VALIDANDO):
            return caso

        snapshot = caso.to_dict(True)
        run_info_analise = snapshot.get("run_analise")
        run_info_validacao = snapshot.get("run_validacao")

        # Determina qual run consultar: validacao tem prioridade se existir
        run_info = run_info_validacao if run_info_validacao else run_info_analise
        if not run_info or not run_info.get("run_id"):
            # run_id vazio: a thread ainda está criando o run — volta pendente
            return caso

        run_id = run_info["run_id"]
        thread_id = run_info.get("thread_id", "")
        fase = run_info.get("agente", "analise")
        # Recupera o operacao_id original; gera novo apenas se não estiver registrado
        operacao_id = run_info.get("operacao_id") or f"OP-{uuid.uuid4().hex[:12].upper()}"

        # Evita consulta IBM concorrente com o worker de background:
        # se o worker atualizou recentemente (dentro de _POLL_INTERVALO_INICIAL_S),
        # devolve o estado local sem fazer uma nova requisição IBM.
        # Dois consultar_run_pendente simultâneos (frontend) podem ambos chamar IBM —
        # isso é correto; o serviço usa lock interno para evitar dupla aplicação.
        t_ultima_worker = self._ultima_consulta_worker.get(run_id, 0.0)
        t_agora = time.monotonic()
        if t_agora - t_ultima_worker < _POLL_INTERVALO_INICIAL_S:
            logger.debug(
                "[poll] consultar_run_pendente: worker consultou ha %.1fs — estado local tx=%s",
                t_agora - t_ultima_worker,
                transacao_id,
            )
            return caso

        try:
            run_status = self._ibm.consultar_run(run_id)
        except Exception as exc:
            logger.warning("Erro ao consultar run %s: %s", run_id, exc)
            return caso

        estado_run = (run_status.get("status") or "").lower()
        if estado_run not in {"completed", "failed", "cancelled", "error"}:
            return caso  # ainda em execução

        if fase in ("analise",):
            try:
                return self._processar_resultado_analise(
                    transacao_id, operacao_id, run_id, thread_id, run_status
                )
            except Exception as exc:
                logger.error("Erro ao processar resultado de análise via poll: %s", exc)
                return caso
        else:
            try:
                return self._processar_resultado_validacao(
                    transacao_id,
                    operacao_id,
                    run_id,
                    thread_id,
                    run_status,
                    [t for t in caso.conversa if t.get("papel") == "cliente"],
                )
            except Exception as exc:
                logger.error("Erro ao processar resultado de validação via poll: %s", exc)
                return caso


# ---------------------------------------------------------------------------
# Mensagens para os agentes
# ---------------------------------------------------------------------------


def _prompt_analise(transacao_id: str) -> str:
    # Mensagem curta: o YAML já contém o fluxo completo, o schema e os exemplos.
    # Repetir o schema aqui acrescenta bytes sem benefício — o agente segue as instruções.
    return (
        f"Analise a transacao: {transacao_id}\n"
        f"1. consultar_contexto(transacao_id='{transacao_id}')\n"
        "2. Classifique o risco (baixo/medio/alto).\n"
        f"3. registrar_analise(transacao_id='{transacao_id}', analise_json=<JSON v2>)\n"
        "4. Responda APENAS com o JSON da analise."
    )


def _prompt_validacao(transacao_id: str, conversa: list[dict]) -> str:
    respostas_cliente = [t for t in conversa if t.get("papel") == "cliente"]
    perguntas_agente = [t for t in conversa if t.get("papel") == "agente"]

    # ── 1.º turno: ainda não há resposta do cliente ──────────────────────────
    if not respostas_cliente:
        return (
            f"Valide a transacao com ID: {transacao_id}\n\n"
            "Voce recebeu a analise de risco inicial (risco medio) nas variaveis de contexto.\n"
            "Ainda NAO ha resposta do cliente.\n\n"
            "Tarefa: formule UMA pergunta clara e direta ao cliente para confirmar se ele\n"
            "reconhece a transacao. Use os dados da analise (estabelecimento, valor, data).\n\n"
            "NAO chame executar_decisao neste turno.\n\n"
            "Caminhos de evidencia validos: /valor, /canal, /estabelecimento/nome, /dispositivo/novo.\n"
            "NAO use o prefixo /transacao/ — ele nao existe nessa estrutura.\n\n"
            "Responda APENAS com o JSON do contrato versao 2:\n"
            f'{{"versao": 2, "transacao_id": "{transacao_id}", '
            f'"desfecho": "aguardar_cliente", '
            f'"pergunta": "<pergunta clara e direta ao cliente>", '
            f'"evidencias": [{{"caminho": "/valor", "descricao": "Valor da transacao"}}], '
            f'"justificativa": "Aguardando confirmacao do cliente."}}'
        )

    # ── Turnos seguintes: avaliar respostas já recebidas ─────────────────────
    turnos = []
    for turno in conversa:
        papel = turno.get("papel", "")
        if turno.get("sem_resposta"):
            texto = "[sem resposta]"
        else:
            texto = turno.get("texto") or "[sem resposta]"
        turnos.append(f"  {papel.upper()}: {texto}")
    historico = "\nHistorico da conversa:\n" + "\n".join(turnos)

    n_perguntas = len(perguntas_agente)
    pode_perguntar = n_perguntas < 3

    instrucao_final = (
        "Com base no historico acima, tome uma decisao:\n"
        "  - Cliente confirmou claramente → desfecho: 'autorizar', pergunta: null\n"
        "  - Cliente negou ou contradisse → desfecho: 'bloquear', pergunta: null\n"
        "  - Resposta ausente ([sem resposta]) → desfecho: 'pendente', pergunta: null\n"
    )
    if pode_perguntar:
        instrucao_final += (
            "  - Resposta ambigua e ha espaco para mais perguntas → "
            "desfecho: 'aguardar_cliente', pergunta: '<nova pergunta>'\n\n"
            "Se o desfecho for aguardar_cliente, NAO chame executar_decisao.\n"
        )
    else:
        instrucao_final += (
            "  - Limite de perguntas atingido: se ainda ambiguo → "
            "desfecho: 'pendente', pergunta: null\n\n"
        )
    instrucao_final += (
        "Caminhos de evidencia validos: /valor, /canal, /estabelecimento/nome, /dispositivo/novo.\n"
        "NAO use o prefixo /transacao/ — ele nao existe nessa estrutura.\n\n"
        "Para desfechos finais (autorizar, bloquear, pendente), chame executar_decisao.\n\n"
        "Responda APENAS com o JSON do contrato versao 2:\n"
        f'{{"versao": 2, "transacao_id": "{transacao_id}", '
        f'"desfecho": "<aguardar_cliente|autorizar|bloquear|pendente>", '
        f'"pergunta": "<pergunta ou null>", '
        f'"evidencias": [{{"caminho": "/valor", "descricao": "..."}}], '
        f'"justificativa": "..."}}'
    )

    return f"Valide a transacao com ID: {transacao_id}\n{historico}\n\n" + instrucao_final


def _condensar_analise(analise) -> str:
    """
    Produz um JSON condensado da análise para context_variables da validação.

    Preserva os campos necessários ao agente de validação:
    - versao, transacao_id, risco, acao: para contextualizar a decisão
    - evidencias: apenas caminhos (sem descrição longa) — reduz ~30% do payload
    - justificativa: truncada a 120 caracteres — suficiente para orientar a pergunta

    A ferramenta executar_decisao usa o JSON de *validação*, não de análise.
    O hash do comprovante é calculado sobre a validação completa.
    """
    dados = analise.model_dump()
    evidencias_slim = [
        {"caminho": e.get("caminho", ""), "descricao": (e.get("descricao") or "")[:60]}
        for e in (dados.get("evidencias") or [])
    ]
    just = dados.get("justificativa", "")
    condensado = {
        "versao": dados.get("versao", 2),
        "transacao_id": dados.get("transacao_id", ""),
        "risco": dados.get("risco", ""),
        "acao": dados.get("acao", ""),
        "evidencias": evidencias_slim,
        "justificativa": just[:120] + ("..." if len(just) > 120 else ""),
    }
    return json.dumps(condensado, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Context variables
# ---------------------------------------------------------------------------


def _montar_ctx_analise(operacao_id: str, transacao_id: str) -> dict:
    return {
        "fraude_operacao_id": operacao_id,
        "fraude_transacao_id": transacao_id,
        "fraude_fase": "analise",
    }


def _montar_ctx_validacao(
    operacao_id: str,
    transacao_id: str,
    analise_json: str,
    respostas_json: str,
    *,
    thread_existente: bool = False,
) -> dict:
    """
    Monta context_variables para o agente de validação.

    O contexto pertence à run, mesmo quando a conversa reutiliza uma thread.
    A análise e as respostas são reenviadas em cada turno para as ferramentas.
    """
    ctx: dict = {
        "fraude_operacao_id": operacao_id,
        "fraude_transacao_id": transacao_id,
        "fraude_fase": "validacao",
    }
    ctx["fraude_analise"] = analise_json
    ctx["fraude_respostas_cliente"] = respostas_json
    return ctx


# ---------------------------------------------------------------------------
# Normalização de respostas do agente
# ---------------------------------------------------------------------------


def _normalizar_evidencias(raw_evidencias: list) -> list[dict]:
    """
    Normaliza a lista de evidências retornada pelo agente.

    O modelo Frontier pode retornar evidências como strings no formato
    "/caminho: descricao" em vez de objetos {caminho, descricao}.
    Esta função converte strings no formato esperado pelo contrato.
    """
    resultado = []
    for item in raw_evidencias:
        if isinstance(item, dict):
            resultado.append(item)
        elif isinstance(item, str):
            # Tenta extrair "/caminho: descricao"
            s = item.strip()
            if s.startswith("/") and ": " in s:
                idx = s.index(": ")
                caminho = s[:idx].strip()
                descricao = s[idx + 2 :].strip()
                resultado.append({"caminho": caminho, "descricao": descricao or item})
            elif s.startswith("/"):
                resultado.append(
                    {"caminho": s, "descricao": f"Campo {s} relevante para a análise."}
                )
            else:
                # Não tem formato de pointer — pula (o contrato vai rejeitar de qualquer forma)
                raise ErroCriterioComprovante("Evidência com formato inválido.")
        else:
            raise ErroCriterioComprovante("Evidência com tipo inválido.")
    return resultado


# ---------------------------------------------------------------------------
# Aguarda run com orçamento de tempo (relógio monotônico)
# ---------------------------------------------------------------------------


def _aguardar_run_com_timeout(
    ibm,
    run_id: str,
    timeout_s: float,
    ts_worker: dict[str, float] | None = None,
) -> dict:
    """
    Aguarda a conclusão de um run usando relógio monotônico como orçamento.

    Intervalos de poll crescem linearmente (backoff simples), limitados a
    _POLL_INTERVALO_MAX_S, para evitar consultas excessivas no início e
    atrasos desnecessários para detectar conclusões tardias.

    Nunca cancela o run remoto ao esgotar o orçamento local.

    ts_worker: se fornecido, atualizado com o timestamp de cada consulta IBM,
    para que consultar_run_pendente evite consultas concorrentes.
    """
    t_limite = time.monotonic() + timeout_s
    intervalo = _POLL_INTERVALO_INICIAL_S
    n_consultas = 0

    ultimo_status: dict = {}

    while True:
        # Verifica o prazo ANTES de iniciar a requisição HTTP.
        # Evita consulta extra quando o orçamento já foi consumido pelo sleep anterior.
        if time.monotonic() >= t_limite:
            logger.warning(
                "[diag] poll timeout run_id=%s n_consultas=%d orcamento=%.0fs",
                run_id,
                n_consultas,
                timeout_s,
            )
            return ultimo_status

        t_consulta = time.monotonic()
        # CORREÇÃO C: Passa o orçamento restante ao consultar_run para limitar o timeout HTTP.
        # Todos os adaptadores (AdaptadorReal, AdaptadorFake e fakes de teste) aceitam timeout.
        # Não há mínimo artificial — se restam 0,2s, a requisição recebe 0,2s.
        orcamento_restante = t_limite - t_consulta
        if orcamento_restante <= 0:
            return ultimo_status
        # Registra timestamp antes da chamada para que consultar_run_pendente
        # saiba que o worker está ativo e evite consultas concorrentes.
        if ts_worker is not None:
            ts_worker[run_id] = t_consulta
        ultimo_status = ibm.consultar_run(run_id, timeout=orcamento_restante)
        n_consultas += 1
        estado = (ultimo_status.get("status") or "").lower()

        logger.debug(
            "[diag] poll run_id=%s consulta=%d status=%s t=%.2fs",
            run_id,
            n_consultas,
            estado,
            time.monotonic() - t_consulta,
        )

        if estado in {"completed", "failed", "cancelled", "error"}:
            logger.info(
                "[diag] poll concluido run_id=%s status=%s n_consultas=%d",
                run_id,
                estado,
                n_consultas,
            )
            return ultimo_status

        if time.monotonic() >= t_limite:
            logger.warning(
                "[diag] poll timeout run_id=%s n_consultas=%d orcamento=%.0fs",
                run_id,
                n_consultas,
                timeout_s,
            )
            return ultimo_status  # retorna status atual sem cancelar o run

        # Aguarda o próximo intervalo, sem ultrapassar o orçamento restante
        restante = t_limite - time.monotonic()
        espera = min(intervalo, restante, _POLL_INTERVALO_MAX_S)
        if espera > 0:
            time.sleep(espera)

        # Incremento linear até o máximo
        intervalo = min(intervalo + 1.0, _POLL_INTERVALO_MAX_S)


# ---------------------------------------------------------------------------
# Verificação de comprovantes
# ---------------------------------------------------------------------------


def _comprovante_ferramenta(run_status: dict, nome: str, transacao_id: str) -> dict:
    """Exige chamada e retorno vinculados pelo tool_call_id no histórico desta run."""
    mensagem = (run_status.get("result") or {}).get("data", {}).get("message", {})
    chamadas, retornos = {}, []
    for etapa in mensagem.get("step_history", []):
        for detalhe in etapa.get("step_details", []):
            if detalhe.get("type") == "tool_calls":
                for chamada in detalhe.get("tool_calls", []):
                    chamadas[chamada.get("id")] = chamada
            elif detalhe.get("type") == "tool_call":
                chamadas[detalhe.get("tool_call_id")] = detalhe
            elif detalhe.get("type") == "tool_response" and detalhe.get("name") == nome:
                retornos.append(detalhe)
    for retorno in reversed(retornos):
        chamada = chamadas.get(retorno.get("tool_call_id"))
        if not chamada or chamada.get("name") != nome:
            continue
        if chamada.get("args", {}).get("transacao_id") != transacao_id:
            continue
        try:
            conteudo = retorno.get("content")
            dados = json.loads(conteudo) if isinstance(conteudo, str) else conteudo
        except (ValueError, TypeError):
            continue
        if isinstance(dados, dict) and dados.get("status") == "ok":
            return dados
    raise ErroCriterioComprovante(f"Chamada bem-sucedida de {nome} ausente nesta execução.")


def _verificar_comprovante_analise(run_status, operacao_id, transacao_id, analise):
    contexto = _comprovante_ferramenta(run_status, "consultar_contexto", transacao_id)
    if contexto.get("transacao", {}).get("id") != transacao_id:
        raise ErroCriterioComprovante("Contexto pertence a outra transação.")
    comp = _comprovante_ferramenta(run_status, "registrar_analise", transacao_id)
    esperado = {
        "transacao_id": transacao_id,
        "fase": "analise",
        "risco": analise.risco.value,
        "acao": analise.acao.value,
        "hash_analise": calcular_hash(analise.model_dump()),
    }
    if any(comp.get(k) != v for k, v in esperado.items()):
        raise ErroCriterioComprovante("Comprovante de análise diverge da resposta final.")


def _verificar_comprovante_decisao(run_status, operacao_id, transacao_id, validacao):
    comp = _comprovante_ferramenta(run_status, "executar_decisao", transacao_id)
    esperado = {
        "transacao_id": transacao_id,
        "fase": "validacao",
        "simulado": True,
        "desfecho": validacao.desfecho.value,
        "hash_validacao": calcular_hash(validacao.model_dump()),
    }
    if any(comp.get(k) != v for k, v in esperado.items()):
        raise ErroCriterioComprovante("Comprovante de decisão diverge da resposta final.")
