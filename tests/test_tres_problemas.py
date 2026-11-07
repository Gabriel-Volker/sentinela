"""
tests/test_tres_problemas.py — Regressões para os três problemas confirmados.

Todos os testes chamam código de produção diretamente:
  - Coordenador, _aguardar_run_com_timeout, ServicoCasos
  - Dependências externas substituídas com fakes de threading.Event

A. Run anterior consultado durante criação do segundo run
   → pergunta registrada duas vezes

B. Status terminal (failed/cancelled/error) mantém PENDENTE_EXECUCAO
   → interface aguarda execução que já terminou

C. Orçamento local não chega ao timeout HTTP
   → consultar_run pode demorar mais que o orçamento restante
"""

from __future__ import annotations

from ibm.orchestrate import AdaptadorFake as IBMFake

import json
import threading
import time

import pytest

from fraude.coordenador import Coordenador, _aguardar_run_com_timeout
from fraude.estados import EstadoCaso
from fraude.servico import ServicoCasos

# ---------------------------------------------------------------------------
# Helpers compartilhados
# ---------------------------------------------------------------------------


def _tx_id() -> str:
    import json as _j
    from pathlib import Path

    p = Path("dados/mock/operacional/candidatas.json")
    if not p.exists():
        pytest.skip("candidatas.json ausente")
    data = _j.loads(p.read_bytes())
    if not data:
        pytest.skip("candidatas.json vazio")
    return data[0]["id"]


def _analise(tx_id: str, risco: str = "medio") -> dict:
    acao = {"baixo": "autorizar", "medio": "validar", "alto": "bloquear"}[risco]
    return {
        "versao": 2,
        "transacao_id": tx_id,
        "risco": risco,
        "acao": acao,
        "evidencias": [{"caminho": "/valor", "descricao": "Valor."}],
        "informacoes_ausentes": [],
        "justificativa": f"Risco {risco}.",
    }


def _validacao(tx_id: str, desfecho: str = "aguardar_cliente", pergunta: str | None = None) -> dict:
    if desfecho == "aguardar_cliente" and pergunta is None:
        pergunta = "Você reconhece esta compra?"
    return {
        "versao": 2,
        "transacao_id": tx_id,
        "desfecho": desfecho,
        "pergunta": pergunta,
        "evidencias": [{"caminho": "/valor", "descricao": "Valor verificado."}],
        "justificativa": f"Desfecho: {desfecho}.",
    }


def _aguardar_estado(svc: ServicoCasos, tx_id: str, estado, timeout: float = 4.0):
    prazo = time.monotonic() + timeout
    while time.monotonic() < prazo:
        if svc.obter_caso(tx_id).estado == estado:
            break
        time.sleep(0.02)
    return svc.obter_caso(tx_id)


# ---------------------------------------------------------------------------
# PROBLEMA A — Run anterior consultado durante criação do segundo run
# ---------------------------------------------------------------------------


class FakeRunSlotA:
    """
    Simula a janela de corrida do Problema A.

    Sequência de criar_run:
      1. analise    → run-analise-001
      2. validacao1 → run-val-001   (1.ª pergunta — já processada)
      3. validacao2 → BLOQUEIA até bloquear.set()  (criação em andamento)

    poll chega na janela (3): run_validacao ainda aponta para run-val-001.
    """

    def __init__(
        self,
        tx_id: str,
        bloquear: threading.Event,
        terceiro_criado: threading.Event,
    ) -> None:
        self._tx = tx_id
        self._bloquear = bloquear
        self._terceiro_criado = terceiro_criado
        self._n_criar = 0
        self._respostas: dict[str, dict] = {}
        self._runs_consultados: list[str] = []
        self._lock = threading.Lock()

    def registrar_resposta(self, run_id: str, resp: dict) -> None:
        with self._lock:
            self._respostas[run_id] = resp

    def criar_run(self, agente_id, mensagem, thread_id, context_variables) -> dict:
        with self._lock:
            self._n_criar += 1
            n = self._n_criar

        if n == 3:
            # Terceira criação — bloqueia até receber sinal
            self._bloquear.wait(timeout=5.0)
            run_id = "run-val-002"
            self._terceiro_criado.set()
        elif n == 2:
            run_id = "run-val-001"
        else:
            run_id = "run-analise-001"

        return {"run_id": run_id, "thread_id": f"thr-{run_id}"}

    def consultar_run(self, run_id: str, timeout: float | None = None) -> dict:
        with self._lock:
            self._runs_consultados.append(run_id)
            resp = self._respostas.get(run_id)
        if resp is not None:
            return {**IBMFake.resultado(resp), "status": "completed"}
        return {"status": "running"}

    def aguardar_run(self, run_id, max_tentativas=1):
        return self.consultar_run(run_id)

    def extrair_json_resposta(self, run_status):
        saida = run_status.get("output", "")
        try:
            return json.loads(saida) if saida else None
        except Exception:
            return None


def test_problema_A_poll_durante_criacao_nao_consulta_run_anterior():
    """
    PROBLEMA A (reproduzido): consultar_run_pendente NÃO deve consultar
    run-val-001 quando o backend está criando run-val-002.

    Antes da correção: poll enxerga run_validacao com run_id="run-val-001"
    e o processa novamente → pergunta duplicada.
    Depois da correção: poll detecta que o slot está em criação (run_id vazio
    ou marcado como transitório) e retorna sem processar.
    """
    tx_id = _tx_id()
    bloquear = threading.Event()
    terceiro_criado = threading.Event()

    fake = FakeRunSlotA(tx_id, bloquear, terceiro_criado)
    fake.registrar_resposta("run-analise-001", _analise(tx_id, "medio"))
    fake.registrar_resposta("run-val-001", _validacao(tx_id, "aguardar_cliente", "Reconhece?"))
    fake.registrar_resposta("run-val-002", _validacao(tx_id, "autorizar"))

    svc = ServicoCasos()
    coord = Coordenador(
        svc, fake, agente_analise_id="fake-analise", agente_validacao_id="fake-validacao"
    )

    # Passo 1+2: análise → 1.ª validação em background
    coord.executar_analise(tx_id, "manual")
    _aguardar_estado(svc, tx_id, EstadoCaso.AGUARDANDO_CLIENTE)
    assert svc.obter_caso(tx_id).estado == EstadoCaso.AGUARDANDO_CLIENTE

    # Passo 3: cliente responde
    svc.registrar_resposta_cliente(tx_id, "Sim, fui eu.", False)

    # Passo 4: inicia criação de run-val-002 em background (bloqueada)
    def _criar_val():
        coord.executar_validacao(tx_id, "Sim, fui eu.")

    t = threading.Thread(target=_criar_val, daemon=True)
    t.start()
    # Aguarda a thread bloquear em criar_run
    time.sleep(0.15)

    # Passo 5: poll chega durante a criação bloqueada
    coord.consultar_run_pendente(tx_id)

    # ANTES da correção: poll consultaria run-val-001 → duplica a pergunta
    # DEPOIS da correção: poll vê run_id="" (slot em criação) → retorna sem processar
    perguntas_no_momento = sum(
        1 for turn in svc.obter_caso(tx_id).conversa if turn.get("papel") == "agente"
    )
    assert perguntas_no_momento == 1, (
        f"Esperado 1 pergunta, encontradas {perguntas_no_momento}. "
        "Poll consultou run-val-001 durante a criação de run-val-002."
    )

    # Passo 6: libera a criação
    bloquear.set()
    t.join(timeout=4.0)


def test_problema_A_resultado_antigo_rejeitado_apos_nova_operacao():
    """
    Processar o resultado de run-val-001 depois que run-val-002 foi registrado
    não deve adicionar pergunta extra.
    """
    tx_id = _tx_id()

    class FakeOrdem:
        def __init__(self):
            self._n_val = 0
            self._lock = threading.Lock()
            self._seq = {
                "run-a": _analise(tx_id, "medio"),
                "run-v-001": _validacao(tx_id, "aguardar_cliente", "Reconhece 1?"),
                "run-v-002": _validacao(tx_id, "autorizar"),
            }

        def criar_run(self, agente_id, mensagem, thread_id, context_variables):
            if "validacao" in agente_id:
                with self._lock:
                    self._n_val += 1
                    run_id = f"run-v-{self._n_val:03}"
            else:
                run_id = "run-a"
            return {"run_id": run_id, "thread_id": f"thr-{run_id}"}

        def consultar_run(self, run_id: str, timeout: float | None = None):
            resp = self._seq.get(run_id)
            if resp:
                return {**IBMFake.resultado(resp), "status": "completed"}
            return {"status": "running"}

        def aguardar_run(self, run_id, max_tentativas=1):
            return self.consultar_run(run_id)

        def extrair_json_resposta(self, run_status):
            saida = run_status.get("output", "")
            try:
                return json.loads(saida) if saida else None
            except Exception:
                return None

    fake = FakeOrdem()
    svc = ServicoCasos()
    coord = Coordenador(
        svc, fake, agente_analise_id="fake-analise", agente_validacao_id="fake-validacao"
    )

    # Análise + 1.ª validação (background)
    coord.executar_analise(tx_id, "manual")
    _aguardar_estado(svc, tx_id, EstadoCaso.AGUARDANDO_CLIENTE)

    n1 = sum(1 for t in svc.obter_caso(tx_id).conversa if t.get("papel") == "agente")
    assert n1 == 1, f"Esperado 1 pergunta após 1.ª validação, obtido {n1}"

    # 2.ª validação
    svc.registrar_resposta_cliente(tx_id, "Sim.", False)
    coord.executar_validacao(tx_id, "Sim.")

    # Consultar_run_pendente com caso já fora de PENDENTE_EXECUCAO
    coord.consultar_run_pendente(tx_id)

    n_final = sum(1 for t in svc.obter_caso(tx_id).conversa if t.get("papel") == "agente")
    assert n_final <= 1, f"Pergunta duplicada: {n_final} perguntas encontradas."


# ---------------------------------------------------------------------------
# PROBLEMA B — Status terminal mantém PENDENTE_EXECUCAO
# ---------------------------------------------------------------------------


class FakeTerminalStatus:
    """Análise OK; validação retorna status terminal sem JSON."""

    def __init__(self, tx_id: str, status_val: str) -> None:
        self._tx = tx_id
        self._status_val = status_val
        self._n = 0

    def criar_run(self, agente_id, mensagem, thread_id, context_variables):
        self._n += 1
        return {"run_id": f"run-{self._n:03}", "thread_id": f"thr-{self._n:03}"}

    def consultar_run(self, run_id: str, timeout: float | None = None) -> dict:
        if run_id == "run-001":
            return {**IBMFake.resultado(_analise(self._tx, "medio")), "status": "completed"}
        return {"status": self._status_val}

    def aguardar_run(self, run_id, max_tentativas=1):
        return self.consultar_run(run_id)

    def extrair_json_resposta(self, run_status):
        saida = run_status.get("output", "")
        try:
            return json.loads(saida) if saida else None
        except Exception:
            return None


@pytest.mark.parametrize("status_terminal", ["failed", "cancelled", "error"])
def test_problema_B_status_terminal_nao_fica_em_pendente_execucao(status_terminal: str):
    """
    PROBLEMA B: quando a validação retorna failed/cancelled/error,
    o caso deve transitar para PENDENTE — não ficar em PENDENTE_EXECUCAO.
    """
    tx_id = _tx_id()
    fake = FakeTerminalStatus(tx_id, status_terminal)
    svc = ServicoCasos()
    coord = Coordenador(
        svc, fake, agente_analise_id="fake-analise", agente_validacao_id="fake-validacao"
    )

    coord.executar_analise(tx_id, "manual")

    prazo = time.monotonic() + 5.0
    while time.monotonic() < prazo:
        estado = svc.obter_caso(tx_id).estado
        if estado not in (EstadoCaso.VALIDANDO, EstadoCaso.PENDENTE_EXECUCAO):
            break
        time.sleep(0.02)

    estado_final = svc.obter_caso(tx_id).estado

    # ANTES: estado_final == PENDENTE_EXECUCAO
    # DEPOIS: estado_final == PENDENTE
    assert estado_final != EstadoCaso.PENDENTE_EXECUCAO, (
        f"Run terminou com '{status_terminal}' mas caso ficou em PENDENTE_EXECUCAO. "
        f"Estado: {estado_final.value}"
    )
    assert estado_final == EstadoCaso.PENDENTE, (
        f"Status terminal '{status_terminal}' deveria resultar em PENDENTE, "
        f"obtido: {estado_final.value}"
    )


@pytest.mark.parametrize("status_terminal", ["failed", "cancelled", "error"])
def test_problema_B_consultar_run_pendente_encerra_terminal(status_terminal: str):
    """
    consultar_run_pendente deve encerrar o acompanhamento quando o run remoto
    retorna status terminal, sem manter PENDENTE_EXECUCAO.
    """
    from fraude.contratos import parsear_analise
    from fraude.servico import gerar_comprovante

    tx_id = _tx_id()

    class FakePendenteTerminal:
        def criar_run(self, *a, **kw):
            return {"run_id": "run-val-terminal", "thread_id": "thr-t"}

        def consultar_run(self, run_id: str, timeout: float | None = None):
            if run_id in ("run-analise", "run-001"):
                return {**IBMFake.resultado(_analise(tx_id, "baixo")), "status": "completed"}
            return {"status": status_terminal}

        def aguardar_run(self, run_id, max_tentativas=1):
            return self.consultar_run(run_id)

        def extrair_json_resposta(self, run_status):
            saida = run_status.get("output", "")
            try:
                return json.loads(saida) if saida else None
            except Exception:
                return None

    fake = FakePendenteTerminal()
    svc = ServicoCasos()
    coord = Coordenador(
        svc, fake, agente_analise_id="fake-analise", agente_validacao_id="fake-validacao"
    )

    # Setup: análise concluída, run de validação em PENDENTE_EXECUCAO com status terminal
    svc.iniciar_analise(tx_id, "manual")
    a_dados = _analise(tx_id, "baixo")
    analise = parsear_analise(a_dados)
    comp = gerar_comprovante("OP-TEST", tx_id, "analise", a_dados)
    svc.registrar_resultado_analise(tx_id, analise, {"run_id": "run-analise"}, comp)
    svc.registrar_run_pendente(
        tx_id,
        {
            "run_id": "run-val-terminal",
            "thread_id": "thr-t",
            "agente": "validacao",
            "operacao_id": "OP-VAL-TEST",
        },
        "validacao",
    )

    # consultar_run_pendente deve detectar terminal e encerrar
    caso = coord.consultar_run_pendente(tx_id)

    # ANTES: caso.estado == PENDENTE_EXECUCAO (mantém espera)
    # DEPOIS: caso.estado != PENDENTE_EXECUCAO (acompanhamento encerrado)
    assert caso.estado != EstadoCaso.PENDENTE_EXECUCAO, (
        f"consultar_run_pendente manteve PENDENTE_EXECUCAO para run com "
        f"status '{status_terminal}'. Deve encerrar o acompanhamento."
    )


# ---------------------------------------------------------------------------
# PROBLEMA C — Orçamento local não chega ao timeout HTTP
# ---------------------------------------------------------------------------


def test_problema_C_consultar_run_aceita_timeout():
    """
    PROBLEMA C: ibm.orchestrate.consultar_run deve aceitar parâmetro de timeout
    para que o orçamento restante chegue à camada HTTP.
    """
    import inspect

    from ibm import orchestrate as _orch

    sig = inspect.signature(_orch.consultar_run)
    params = list(sig.parameters.keys())

    # ANTES da correção: params == ['run_id']
    # DEPOIS da correção: params inclui 'timeout' ou 'timeout_s'
    assert "timeout" in params or "timeout_s" in params, (
        f"ibm.orchestrate.consultar_run não aceita parâmetro de timeout. "
        f"Parâmetros: {params}. "
        "O orçamento restante do polling não chega ao timeout HTTP da requisição GET."
    )


def test_problema_C_get_aceita_timeout():
    """
    ibm.orchestrate._get deve aceitar parâmetro de timeout para poder usar
    o orçamento restante em vez do valor fixo de 30s.
    """
    import inspect

    from ibm import orchestrate as _orch

    sig = inspect.signature(_orch._get)
    params = list(sig.parameters.keys())

    assert "timeout" in params, (
        f"ibm.orchestrate._get não aceita parâmetro timeout. "
        f"Parâmetros: {params}. "
        "O timeout HTTP de 30s ignora o orçamento local."
    )


def test_problema_C_coordenador_passa_orcamento_ao_consultar_run():
    """
    _aguardar_run_com_timeout deve passar o tempo restante a consultar_run,
    não usar o timeout padrão fixo quando o orçamento for menor.
    """
    timeouts_recebidos: list[float] = []

    class FakeIbmComTimeout:
        def consultar_run(self, run_id: str, timeout: float | None = None) -> dict:
            timeouts_recebidos.append(timeout if timeout is not None else -1.0)
            return {"status": "running"}

        def aguardar_run(self, run_id, max_tentativas=1):
            return self.consultar_run(run_id)

        def extrair_json_resposta(self, s):
            return None

    fake = FakeIbmComTimeout()
    import fraude.coordenador as _coord_mod

    relogio = [0.0]

    def mono():
        return relogio[0]

    def sleep_avanca(s):
        relogio[0] += s

    orig_mono = _coord_mod.time.monotonic
    orig_sleep = _coord_mod.time.sleep
    _coord_mod.time.monotonic = mono  # type: ignore
    _coord_mod.time.sleep = sleep_avanca  # type: ignore
    try:
        _aguardar_run_com_timeout(fake, "run-c-timeout", 5.0)
    finally:
        _coord_mod.time.monotonic = orig_mono
        _coord_mod.time.sleep = orig_sleep

    # ANTES: timeouts_recebidos[0] == -1.0 (timeout não passado)
    # DEPOIS: timeouts_recebidos[0] > 0 (orçamento restante passado)
    assert len(timeouts_recebidos) >= 1
    assert timeouts_recebidos[0] > 0, (
        f"_aguardar_run_com_timeout não passou timeout ao consultar_run. "
        f"Recebido: {timeouts_recebidos[0]}"
    )
    # O primeiro timeout deve ser ≤ 5.0 (o orçamento completo)
    assert timeouts_recebidos[0] <= 5.0 + 0.01


# ---------------------------------------------------------------------------
# Testes adicionais: _aguardar_run_com_timeout (código produtivo)
# ---------------------------------------------------------------------------


def test_aguardar_run_com_timeout_retorna_imediato_em_completed():
    """Retorna imediatamente em completed, sem dormir."""
    import fraude.coordenador as _coord

    class FakeImediato:
        def consultar_run(self, run_id, timeout=None):
            return {"status": "completed", "output": "{}"}

        def aguardar_run(self, run_id, max_tentativas=1):
            return self.consultar_run(run_id)

        def extrair_json_resposta(self, s):
            return None

    t0 = time.monotonic()
    status = _coord._aguardar_run_com_timeout(FakeImediato(), "run-ok", 30.0)
    elapsed = time.monotonic() - t0

    assert status["status"] == "completed"
    assert elapsed < 1.0, f"Não deveria dormir para completed; demorou {elapsed:.2f}s"


def test_aguardar_run_com_timeout_nao_dorme_em_failed():
    """failed é terminal — retorna sem dormir."""
    import fraude.coordenador as _coord_mod

    sleeps: list[float] = []

    class FakeFailed:
        def consultar_run(self, run_id, timeout=None):
            return {"status": "failed"}

        def aguardar_run(self, run_id, max_tentativas=1):
            return self.consultar_run(run_id)

        def extrair_json_resposta(self, s):
            return None

    orig_sleep = _coord_mod.time.sleep
    _coord_mod.time.sleep = lambda s: sleeps.append(s)  # type: ignore
    try:
        status = _coord_mod._aguardar_run_com_timeout(FakeFailed(), "run-f", 30.0)
    finally:
        _coord_mod.time.sleep = orig_sleep

    assert status["status"] == "failed"
    assert len(sleeps) == 0, f"Não deve dormir para status terminal failed; sleeps: {sleeps}"


def test_aguardar_run_com_timeout_com_relogio_injetado():
    """Com relógio injetado, orçamento expira após 1 consulta (sleep avança o relógio)."""
    import fraude.coordenador as _coord_mod

    n: list[int] = [0]
    relogio = [0.0]

    def mono():
        return relogio[0]

    def sleep_avanca(s):
        relogio[0] += s

    class FakeRunning:
        def consultar_run(self, run_id, timeout=None):
            n[0] += 1
            return {"status": "running"}

        def aguardar_run(self, run_id, max_tentativas=1):
            return self.consultar_run(run_id)

        def extrair_json_resposta(self, s):
            return None

    orig_mono = _coord_mod.time.monotonic
    orig_sleep = _coord_mod.time.sleep
    _coord_mod.time.monotonic = mono  # type: ignore
    _coord_mod.time.sleep = sleep_avanca  # type: ignore
    try:
        status = _coord_mod._aguardar_run_com_timeout(FakeRunning(), "run-r", 5.0)
    finally:
        _coord_mod.time.monotonic = orig_mono
        _coord_mod.time.sleep = orig_sleep

    assert status["status"] == "running"
    assert 1 <= n[0] <= 3, f"Consultas inesperadas: {n[0]}"
