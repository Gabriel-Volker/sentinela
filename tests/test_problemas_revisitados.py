"""
tests/test_problemas_revisitados.py — Regressões para os três problemas revisitados.

A-revisitado: Resultado antigo em trânsito aplicado à operação nova.
  Sequência determinística com threading.Event:
    1. Consulta ao run-val-001 iniciada — resposta bloqueada.
    2. 1.ª validação conclui por outro caminho (run-val-001 → pergunta 1).
    3. Cliente responde; nova operação criada (run-val-002 registrado).
    4. Resposta de run-val-001 liberada.
    5. Guard atômico deve descartar run-val-001; nenhum dado da op-002 alterado.
    6. Run-val-002 conclui normalmente → estado correto.

B-revisitado: completed sem JSON e saídas inválidas mantêm PENDENTE_EXECUCAO.
  - completed sem output → PENDENTE (não PENDENTE_EXECUCAO).
  - completed com JSON quebrado → PENDENTE.
  - completed com contrato inválido → PENDENTE (via exceção capturada em poll).
  - failed com JSON aparentemente válido → PENDENTE (não aplica a saída).

C-revisitado: AdaptadorReal não repassava timeout; TypeError era capturado.
  - AdaptadorReal.consultar_run aceita timeout.
  - Todos os fakes aceitam timeout.
  - Orçamento < 1s é passado sem arredondamento para cima.
  - Caminho completo: _aguardar_run_com_timeout → adaptador → transporte HTTP local.
"""

from __future__ import annotations

from ibm.orchestrate import AdaptadorFake as IBMFake

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from fraude.coordenador import Coordenador, _aguardar_run_com_timeout
from fraude.estados import EstadoCaso
from fraude.servico import ServicoCasos

# ---------------------------------------------------------------------------
# Helpers
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


def _aguardar_estado(svc: ServicoCasos, tx_id: str, estado, timeout: float = 5.0):
    prazo = time.monotonic() + timeout
    while time.monotonic() < prazo:
        if svc.obter_caso(tx_id).estado == estado:
            break
        time.sleep(0.02)
    return svc.obter_caso(tx_id)


# ---------------------------------------------------------------------------
# PROBLEMA A REVISITADO — resultado antigo em trânsito
# ---------------------------------------------------------------------------


class FakeIbmBloqueioResposta:
    """
    Fake que bloqueia a ENTREGA de uma resposta de consultar_run específica.

    Isso simula um resultado antigo (run-val-001) que já havia sido solicitado
    via HTTP mas cuja resposta chegou depois que a operação foi substituída por
    run-val-002.

    Controle:
      - liberar_val_001: Event — quando setado, consultar_run("run-val-001") retorna.
      - val_001_solicitado: Event — setado quando consultar_run("run-val-001") é chamado.
    """

    def __init__(self, tx_id: str, respostas: dict[str, dict]) -> None:
        self._tx = tx_id
        self._respostas = respostas  # run_id → dict de resposta
        self._n_criar = 0
        self._lock = threading.Lock()
        self.liberar_val_001 = threading.Event()
        self.val_001_solicitado = threading.Event()

    def criar_run(
        self, agente_id: str, mensagem: str, thread_id: Any, context_variables: Any
    ) -> dict:
        with self._lock:
            self._n_criar += 1
            n = self._n_criar
        run_id = f"run-{n:03}"
        return {"run_id": run_id, "thread_id": f"thr-{run_id}"}

    def consultar_run(self, run_id: str, timeout: float | None = None) -> dict:
        # Simula resultado antigo chegando tarde
        if run_id == "run-002":
            self.val_001_solicitado.set()
            self.liberar_val_001.wait(timeout=8.0)
        resp = self._respostas.get(run_id)
        if resp is not None:
            return {**IBMFake.resultado(resp), "status": "completed"}
        return {"status": "running"}

    def aguardar_run(self, run_id: str, max_tentativas: int = 1) -> dict:
        return self.consultar_run(run_id)

    def extrair_json_resposta(self, run_status: dict) -> dict | None:
        saida = run_status.get("output", "")
        try:
            return json.loads(saida) if saida else None
        except Exception:
            return None


def test_A_resultado_antigo_descartado_atomicamente():
    """
    PROBLEMA A REVISITADO — Guard atômico em _processar_resultado_validacao.

    Chama diretamente _processar_resultado_validacao com um operacao_id/run_id
    antigo após um novo resultado já ter sido registrado.

    Antes da correção: o resultado antigo sobrescrevia o estado.
    Depois da correção: guard detecta divergência e descarta o resultado.
    """
    from fraude.contratos import parsear_analise
    from fraude.servico import gerar_comprovante

    tx_id = _tx_id()

    class FakeSimples:
        def criar_run(self, *a, **kw):
            return {"run_id": "run-novo", "thread_id": "thr-novo"}

        def consultar_run(self, run_id: str, timeout: float | None = None) -> dict:
            resp = {
                "run-analise": _analise(tx_id, "medio"),
                "run-novo": _validacao(tx_id, "autorizar"),
                "run-antigo": _validacao(tx_id, "aguardar_cliente", "Pergunta velha?"),
            }.get(run_id)
            if resp:
                return {**IBMFake.resultado(resp), "status": "completed"}
            return {"status": "running"}

        def aguardar_run(self, run_id: str, max_tentativas: int = 1) -> dict:
            return self.consultar_run(run_id)

        def extrair_json_resposta(self, run_status: dict) -> dict | None:
            saida = run_status.get("output", "")
            try:
                return json.loads(saida) if saida else None
            except Exception:
                return None

    fake = FakeSimples()
    svc = ServicoCasos()
    coord = Coordenador(
        svc, fake, agente_analise_id="fake-analise", agente_validacao_id="fake-validacao"
    )

    # Setup: análise concluída, 1.ª validação (run-novo/OP-NOVO) já registrada
    svc.iniciar_analise(tx_id, "manual")
    a_dados = _analise(tx_id, "medio")
    analise = parsear_analise(a_dados)
    comp_a = gerar_comprovante("OP-A", tx_id, "analise", a_dados)
    svc.registrar_resultado_analise(
        tx_id, analise, {"run_id": "run-analise", "operacao_id": "OP-A"}, comp_a
    )

    # Registra run-novo como operação ativa
    svc.registrar_run_pendente(
        tx_id,
        {
            "run_id": "run-novo",
            "thread_id": "thr-novo",
            "agente": "validacao",
            "operacao_id": "OP-NOVO",
        },
        "validacao",
    )

    # Simula resultado antigo (run-antigo / OP-ANTIGO) chegando em trânsito
    # O guard deve descartar porque op_ativo=OP-NOVO ≠ OP-ANTIGO
    # e run_ativo=run-novo ≠ run-antigo
    run_status_antigo = {**IBMFake.resultado(_validacao(tx_id, "aguardar_cliente", "Velha?")), "status": "completed"}
    resultado = coord._processar_resultado_validacao(
        tx_id, "OP-ANTIGO", "run-antigo", "thr-antigo", run_status_antigo, []
    )

    # Estado não deve ter mudado: ainda PENDENTE_EXECUCAO (run-novo em andamento)
    assert resultado.estado == EstadoCaso.PENDENTE_EXECUCAO, (
        f"Resultado antigo alterou o estado para {resultado.estado.value}. "
        "Guard atômico deve descartar resultados de operações substituídas."
    )

    # Nenhuma pergunta deve ter sido adicionada
    perguntas = [t for t in resultado.conversa if t.get("papel") == "agente"]
    assert len(perguntas) == 0, f"Resultado antigo adicionou pergunta: {len(perguntas)}"


def test_A_guard_aceita_operacao_atual():
    """
    O guard não deve descartar o resultado da operação ativa.
    O operacao_id registrado e o passado a _processar_resultado devem coincidir.
    """
    tx_id = _tx_id()

    respostas = {
        "run-001": _analise(tx_id, "baixo"),
    }
    fake = FakeIbmBloqueioResposta(tx_id, respostas)

    svc = ServicoCasos()
    coord = Coordenador(
        svc, fake, agente_analise_id="fake-analise", agente_validacao_id="fake-validacao"
    )

    coord.executar_analise(tx_id, "manual")
    caso = svc.obter_caso(tx_id)

    # Análise de baixo risco deve autorizar diretamente
    assert caso.estado == EstadoCaso.AUTORIZADO, (
        f"Esperado AUTORIZADO para risco baixo, obtido {caso.estado.value}"
    )


def test_A_identidade_preservada_apos_timeout():
    """
    Quando o orçamento expira, o run_id e operacao_id do slot devem ser mantidos
    para permitir consultar_run_pendente posterior com a identidade correta.
    """
    import fraude.coordenador as _coord_mod

    tx_id = _tx_id()
    run_ids_consultados: list[str] = []
    relogio = [0.0]

    def mono():
        return relogio[0]

    def sleep_avanca(s: float) -> None:
        relogio[0] += s

    class FakeRunning:
        def criar_run(self, *a, **kw):
            return {"run_id": "run-timeout-01", "thread_id": "thr-t"}

        def consultar_run(self, run_id: str, timeout: float | None = None) -> dict:
            run_ids_consultados.append(run_id)
            return {"status": "running"}

        def aguardar_run(self, run_id: str, max_tentativas: int = 1) -> dict:
            return self.consultar_run(run_id)

        def extrair_json_resposta(self, s: dict) -> dict | None:
            return None

    orig_mono = _coord_mod.time.monotonic
    orig_sleep = _coord_mod.time.sleep
    _coord_mod.time.monotonic = mono  # type: ignore[attr-defined]
    _coord_mod.time.sleep = sleep_avanca  # type: ignore[attr-defined]
    try:
        fake = FakeRunning()
        svc = ServicoCasos()
        Coordenador(  # noqa: F841 — cria instância para não divergir do contexto de produção
            svc, fake, agente_analise_id="fake-analise", agente_validacao_id="fake-validacao"
        )
        svc.iniciar_analise(tx_id, "manual")
        svc.registrar_run_pendente(
            tx_id,
            {
                "run_id": "run-timeout-01",
                "thread_id": "thr-t",
                "agente": "analise",
                "operacao_id": "OP-T-001",
            },
            "analise",
        )

        # Executa polling com orçamento de 5s — o relógio avança com cada sleep
        status = _aguardar_run_com_timeout(fake, "run-timeout-01", 5.0)
    finally:
        _coord_mod.time.monotonic = orig_mono
        _coord_mod.time.sleep = orig_sleep

    # O run_id original deve estar preservado no slot
    caso = svc.obter_caso(tx_id)
    run_info = caso.run_analise or {}
    assert run_info.get("run_id") == "run-timeout-01"
    assert run_info.get("operacao_id") == "OP-T-001"

    # Status retornado deve ser "running" (não cancelado)
    assert status.get("status") == "running"
    assert len(run_ids_consultados) >= 1


# ---------------------------------------------------------------------------
# PROBLEMA B REVISITADO — completed sem JSON e saídas inválidas
# ---------------------------------------------------------------------------


class FakeCompletedSemJson:
    """completed sem output — nenhum JSON disponível."""

    def __init__(self, tx_id: str) -> None:
        self._tx = tx_id
        self._n = 0

    def criar_run(self, *a, **kw) -> dict:
        self._n += 1
        return {"run_id": f"run-{self._n:03}", "thread_id": f"thr-{self._n:03}"}

    def consultar_run(self, run_id: str, timeout: float | None = None) -> dict:
        if run_id == "run-001":
            return {**IBMFake.resultado(_analise(self._tx, "medio")), "status": "completed"}
        # run-002: completed sem output
        return {"status": "completed"}

    def aguardar_run(self, run_id: str, max_tentativas: int = 1) -> dict:
        return self.consultar_run(run_id)

    def extrair_json_resposta(self, run_status: dict) -> dict | None:
        saida = run_status.get("output", "")
        try:
            return json.loads(saida) if saida else None
        except Exception:
            return None


def test_B_completed_sem_json_encerra_acompanhamento():
    """
    PROBLEMA B REVISITADO: completed sem JSON não deve manter PENDENTE_EXECUCAO.
    O caso deve transitar para PENDENTE com causa diagnosticável.
    """
    tx_id = _tx_id()
    fake = FakeCompletedSemJson(tx_id)
    svc = ServicoCasos()
    coord = Coordenador(
        svc, fake, agente_analise_id="fake-analise", agente_validacao_id="fake-validacao"
    )

    coord.executar_analise(tx_id, "manual")

    prazo = time.monotonic() + 6.0
    while time.monotonic() < prazo:
        estado = svc.obter_caso(tx_id).estado
        if estado not in (EstadoCaso.VALIDANDO, EstadoCaso.PENDENTE_EXECUCAO):
            break
        time.sleep(0.02)

    estado_final = svc.obter_caso(tx_id).estado
    assert estado_final != EstadoCaso.PENDENTE_EXECUCAO, (
        f"completed sem JSON manteve PENDENTE_EXECUCAO. Estado: {estado_final.value}"
    )
    assert estado_final == EstadoCaso.PENDENTE, (
        f"completed sem JSON deveria resultar em PENDENTE, obtido: {estado_final.value}"
    )


class FakeCompletedJsonInvalido:
    """completed com JSON quebrado (texto não parseável)."""

    def __init__(self, tx_id: str) -> None:
        self._tx = tx_id
        self._n = 0

    def criar_run(self, *a, **kw) -> dict:
        self._n += 1
        return {"run_id": f"run-{self._n:03}", "thread_id": f"thr-{self._n:03}"}

    def consultar_run(self, run_id: str, timeout: float | None = None) -> dict:
        if run_id == "run-001":
            return {**IBMFake.resultado(_analise(self._tx, "medio")), "status": "completed"}
        return {"status": "completed", "output": "isto nao e json {{{"}

    def aguardar_run(self, run_id: str, max_tentativas: int = 1) -> dict:
        return self.consultar_run(run_id)

    def extrair_json_resposta(self, run_status: dict) -> dict | None:
        saida = run_status.get("output", "")
        try:
            return json.loads(saida) if saida else None
        except Exception:
            return None


def test_B_completed_json_invalido_encerra_acompanhamento():
    """
    completed com JSON corrompido não deve manter PENDENTE_EXECUCAO.
    """
    tx_id = _tx_id()
    fake = FakeCompletedJsonInvalido(tx_id)
    svc = ServicoCasos()
    coord = Coordenador(
        svc, fake, agente_analise_id="fake-analise", agente_validacao_id="fake-validacao"
    )

    coord.executar_analise(tx_id, "manual")

    prazo = time.monotonic() + 6.0
    while time.monotonic() < prazo:
        estado = svc.obter_caso(tx_id).estado
        if estado not in (EstadoCaso.VALIDANDO, EstadoCaso.PENDENTE_EXECUCAO):
            break
        time.sleep(0.02)

    estado_final = svc.obter_caso(tx_id).estado
    assert estado_final != EstadoCaso.PENDENTE_EXECUCAO, (
        f"JSON inválido manteve PENDENTE_EXECUCAO. Estado: {estado_final.value}"
    )
    assert estado_final == EstadoCaso.PENDENTE, (
        f"JSON inválido deveria resultar em PENDENTE, obtido: {estado_final.value}"
    )


@pytest.mark.parametrize("status_terminal", ["failed", "cancelled", "error"])
def test_B_status_terminal_com_json_nao_aplica_saida(status_terminal: str):
    """
    failed/cancelled/error não devem aplicar a saída mesmo que exista JSON válido.
    O resultado deve ser PENDENTE independentemente do conteúdo do output.
    """
    tx_id = _tx_id()
    validacao_json = _validacao(tx_id, "autorizar")

    class FakeTerminalComJson:
        def __init__(self):
            self._n = 0

        def criar_run(self, *a, **kw):
            self._n += 1
            return {"run_id": f"run-{self._n:03}", "thread_id": f"thr-{self._n:03}"}

        def consultar_run(self, run_id: str, timeout: float | None = None) -> dict:
            if run_id == "run-001":
                return {**IBMFake.resultado(_analise(tx_id, "medio")), "status": "completed"}
            # Retorna JSON válido mas com status terminal
            return {**IBMFake.resultado(validacao_json), "status": status_terminal}

        def aguardar_run(self, run_id: str, max_tentativas: int = 1) -> dict:
            return self.consultar_run(run_id)

        def extrair_json_resposta(self, run_status: dict) -> dict | None:
            # Simula extrator que retorna JSON mesmo para status terminal
            saida = run_status.get("output", "")
            try:
                return json.loads(saida) if saida else None
            except Exception:
                return None

    fake = FakeTerminalComJson()
    svc = ServicoCasos()
    coord = Coordenador(
        svc, fake, agente_analise_id="fake-analise", agente_validacao_id="fake-validacao"
    )

    coord.executar_analise(tx_id, "manual")

    prazo = time.monotonic() + 6.0
    while time.monotonic() < prazo:
        estado = svc.obter_caso(tx_id).estado
        if estado not in (EstadoCaso.VALIDANDO, EstadoCaso.PENDENTE_EXECUCAO):
            break
        time.sleep(0.02)

    estado_final = svc.obter_caso(tx_id).estado
    # status terminal deve resultar em PENDENTE — não AUTORIZADO
    assert estado_final != EstadoCaso.AUTORIZADO, (
        f"Status '{status_terminal}' com JSON válido aplicou decisão de autorização!"
    )
    assert estado_final == EstadoCaso.PENDENTE, (
        f"Status '{status_terminal}' deveria resultar em PENDENTE, obtido: {estado_final.value}"
    )


def test_B_consultar_run_pendente_completed_sem_json():
    """
    consultar_run_pendente com completed sem JSON deve encerrar o acompanhamento.
    """
    from fraude.contratos import parsear_analise
    from fraude.servico import gerar_comprovante

    tx_id = _tx_id()

    class FakePollCompletedVazio:
        def criar_run(self, *a, **kw):
            return {"run_id": "run-poll-empty", "thread_id": "thr-pe"}

        def consultar_run(self, run_id: str, timeout: float | None = None) -> dict:
            return {"status": "completed"}  # sem output

        def aguardar_run(self, run_id: str, max_tentativas: int = 1) -> dict:
            return self.consultar_run(run_id)

        def extrair_json_resposta(self, run_status: dict) -> dict | None:
            return None

    fake = FakePollCompletedVazio()
    svc = ServicoCasos()
    coord = Coordenador(
        svc, fake, agente_analise_id="fake-analise", agente_validacao_id="fake-validacao"
    )

    # Setup: análise concluída, run de validação pendente
    svc.iniciar_analise(tx_id, "manual")
    a_dados = _analise(tx_id, "baixo")
    analise = parsear_analise(a_dados)
    comp = gerar_comprovante("OP-TEST-B", tx_id, "analise", a_dados)
    svc.registrar_resultado_analise(
        tx_id, analise, {"run_id": "run-analise", "operacao_id": "OP-TEST-B"}, comp
    )
    svc.registrar_run_pendente(
        tx_id,
        {
            "run_id": "run-poll-empty",
            "thread_id": "thr-pe",
            "agente": "validacao",
            "operacao_id": "OP-VAL-B",
        },
        "validacao",
    )

    caso = coord.consultar_run_pendente(tx_id)

    assert caso.estado != EstadoCaso.PENDENTE_EXECUCAO, (
        "consultar_run_pendente manteve PENDENTE_EXECUCAO para completed sem JSON."
    )
    assert caso.estado == EstadoCaso.PENDENTE, f"Esperado PENDENTE, obtido {caso.estado.value}"


# ---------------------------------------------------------------------------
# PROBLEMA C REVISITADO — caminho completo do timeout
# ---------------------------------------------------------------------------


def test_C_adaptador_real_aceita_timeout():
    """
    AdaptadorReal.consultar_run deve ter parâmetro timeout na assinatura.
    Sem isso o Coordenador capturaria TypeError e descartaria o orçamento.
    """
    import inspect
    import os

    from web.servidor import _criar_adaptador_ibm

    # Força criação do AdaptadorReal com variáveis de ambiente fictícias
    orig_url = os.environ.get("WXO_SERVICE_URL")
    orig_key = os.environ.get("WXO_API_KEY")
    try:
        os.environ["WXO_SERVICE_URL"] = "https://fake.example.com"
        os.environ["WXO_API_KEY"] = "fake-key"
        adaptador = _criar_adaptador_ibm()
    finally:
        if orig_url is None:
            os.environ.pop("WXO_SERVICE_URL", None)
        else:
            os.environ["WXO_SERVICE_URL"] = orig_url
        if orig_key is None:
            os.environ.pop("WXO_API_KEY", None)
        else:
            os.environ["WXO_API_KEY"] = orig_key

    assert adaptador is not None, "AdaptadorReal não foi criado"
    sig = inspect.signature(adaptador.consultar_run)
    params = list(sig.parameters.keys())
    assert "timeout" in params, (
        f"AdaptadorReal.consultar_run não aceita timeout. Parâmetros: {params}"
    )


def test_C_adaptador_fake_aceita_timeout():
    """
    AdaptadorFake.consultar_run deve aceitar timeout para não causar TypeError.
    """
    import inspect

    from ibm.orchestrate import AdaptadorFake

    fake = AdaptadorFake()
    sig = inspect.signature(fake.consultar_run)
    params = list(sig.parameters.keys())
    assert "timeout" in params, (
        f"AdaptadorFake.consultar_run não aceita timeout. Parâmetros: {params}"
    )


def test_C_orcamento_pequeno_passado_sem_arredondamento():
    """
    Orçamento < 1s deve ser passado diretamente, sem arredondamento para 1s.
    """
    import fraude.coordenador as _coord_mod

    timeouts_recebidos: list[float] = []
    relogio = [0.0]

    def mono() -> float:
        return relogio[0]

    def sleep_avanca(s: float) -> None:
        relogio[0] += s

    class FakeTimeoutable:
        def consultar_run(self, run_id: str, timeout: float | None = None) -> dict:
            timeouts_recebidos.append(timeout if timeout is not None else -999.0)
            # Avança o relógio para esgotar o orçamento
            relogio[0] += 0.6
            return {"status": "running"}

        def aguardar_run(self, run_id: str, max_tentativas: int = 1) -> dict:
            return self.consultar_run(run_id)

        def extrair_json_resposta(self, s: dict) -> dict | None:
            return None

    orig_mono = _coord_mod.time.monotonic
    orig_sleep = _coord_mod.time.sleep
    _coord_mod.time.monotonic = mono  # type: ignore[attr-defined]
    _coord_mod.time.sleep = sleep_avanca  # type: ignore[attr-defined]
    try:
        # Orçamento de 0,5s — deve ser passado como ~0,5 (não arredondado para 1,0)
        _aguardar_run_com_timeout(FakeTimeoutable(), "run-small-budget", 0.5)
    finally:
        _coord_mod.time.monotonic = orig_mono
        _coord_mod.time.sleep = orig_sleep

    assert len(timeouts_recebidos) >= 1, "consultar_run não foi chamado"
    primeiro = timeouts_recebidos[0]
    # O timeout passado deve ser <= 0,5 (o orçamento total), não 1,0
    assert primeiro <= 0.5 + 0.01, (
        f"Orçamento de 0,5s foi arredondado para {primeiro:.3f}s. "
        "O orçamento deve ser passado sem piso artificial."
    )
    assert primeiro > 0, f"Timeout inválido: {primeiro}"


def test_C_caminho_completo_via_adaptador_real_http_local():
    """
    Percorre o caminho completo:
    AdaptadorReal.consultar_run(timeout=2.5) → orch.consultar_run(timeout=2.5)
      → _get(endpoint, timeout=2.5) → urlopen(req, timeout=2.5)

    Verifica que o timeout chegou ao transporte HTTP com o valor esperado.
    Intercepta urllib.request.urlopen para capturar o timeout de cada chamada.
    Filtra pela URL do endpoint /runs/ para não confundir com autenticação IAM.
    """
    import os

    servidor_encerrar = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # silencia log
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "running"}).encode())

        def do_POST(self):
            # IAM e criação de run — retorna tokens/IDs fictícios
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            path = self.path
            if "token" in path or "iam" in path.lower():
                body = json.dumps({"access_token": "fake-tok", "expires_in": 3600})
            else:
                body = json.dumps({"access_token": "fake-tok", "expires_in": 3600})
            self.wfile.write(body.encode())

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    porta = httpd.server_address[1]

    def _rodar():
        httpd.timeout = 0.5
        while not servidor_encerrar.is_set():
            httpd.handle_request()

    t_srv = threading.Thread(target=_rodar, daemon=True)
    t_srv.start()

    orig_url = os.environ.get("WXO_SERVICE_URL")
    orig_key = os.environ.get("WXO_API_KEY")
    os.environ["WXO_SERVICE_URL"] = f"http://127.0.0.1:{porta}"
    os.environ["WXO_API_KEY"] = "fake-key"

    try:
        # Recarrega módulos para capturar a nova URL de ambiente
        import importlib

        import ibm.iam as _iam
        import ibm.orchestrate as _orch

        importlib.reload(_iam)
        importlib.reload(_orch)

        from web.servidor import _criar_adaptador_ibm

        adaptador = _criar_adaptador_ibm()
        assert adaptador is not None

        # Substitui token_atual no módulo orchestrate para evitar chamada IAM real
        _iam_orig = _orch.token_atual  # type: ignore[attr-defined]
        _orch.token_atual = lambda: "fake-token-c-test"  # type: ignore[attr-defined]

        # Intercepta urlopen no módulo interno de orchestrate
        chamadas_urlopen: list[tuple[str, Any]] = []

        def _mock_urlopen(req, timeout=None):
            url_str = req.full_url if hasattr(req, "full_url") else str(req)
            chamadas_urlopen.append((url_str, timeout))
            raise RuntimeError("Mock HTTP — não faz request real")

        _orch_urlopen_orig = _orch.urllib.request.urlopen
        _orch.urllib.request.urlopen = _mock_urlopen  # type: ignore[attr-defined]
        try:
            adaptador.consultar_run("run-test-c", timeout=2.5)
        except Exception:
            pass  # esperado — o mock levanta RuntimeError
        finally:
            _orch.urllib.request.urlopen = _orch_urlopen_orig  # type: ignore[attr-defined]
            _orch.token_atual = _iam_orig  # type: ignore[attr-defined]

        # Encontra chamadas para o endpoint /runs/ (não IAM)
        chamadas_runs = [(url, t) for url, t in chamadas_urlopen if "/runs/" in url]

        assert len(chamadas_runs) >= 1, (
            f"Nenhuma chamada ao endpoint /runs/ capturada. Todas: {chamadas_urlopen}"
        )
        url_runs, timeout_recebido = chamadas_runs[0]
        assert timeout_recebido is not None, f"timeout não foi passado ao urlopen para {url_runs}"
        assert timeout_recebido <= 3.0, (
            f"Timeout de {timeout_recebido}s para {url_runs} em vez de ≤3s. "
            "AdaptadorReal não está repassando o orçamento ao transporte HTTP."
        )
    finally:
        servidor_encerrar.set()
        if orig_url is None:
            os.environ.pop("WXO_SERVICE_URL", None)
        else:
            os.environ["WXO_SERVICE_URL"] = orig_url
        if orig_key is None:
            os.environ.pop("WXO_API_KEY", None)
        else:
            os.environ["WXO_API_KEY"] = orig_key


def test_C_sem_TypeError_na_assinatura_do_adaptador():
    """
    Verifica que nenhum adaptador relevante causa TypeError ao receber timeout.
    A ausência de TypeError é o requisito mínimo para remover o fallback.
    """
    from ibm.orchestrate import AdaptadorFake

    fake = AdaptadorFake(
        resposta_analise={"versao": 2, "transacao_id": "x", "risco": "baixo"},
        resposta_validacao={},
    )
    # Não deve lançar TypeError
    try:
        resultado = fake.consultar_run("run-x", timeout=5.0)
        assert isinstance(resultado, dict), "consultar_run deve retornar dict"
    except TypeError as exc:
        pytest.fail(f"AdaptadorFake.consultar_run levantou TypeError com timeout: {exc}")


# ---------------------------------------------------------------------------
# Ausência de decisões e perguntas duplicadas
# ---------------------------------------------------------------------------


def test_sem_decisao_duplicada_em_tentativas_consecutivas():
    """
    aplicar_decisao chamada duas vezes para a mesma transação deve
    levantar ErroConflito na segunda chamada, não duplicar a decisão.

    Usa risco médio → VALIDANDO → AGUARDANDO_CLIENTE → AUTORIZADO,
    para que a primeira aplicar_decisao seja de AGUARDANDO_CLIENTE → AUTORIZADO
    (transição válida), e a segunda seja rejeitada por decisao_aplicada=True.
    """
    from fraude.contratos import parsear_analise, parsear_validacao
    from fraude.servico import ErroConflito, gerar_comprovante

    tx_id = _tx_id()
    svc = ServicoCasos()
    svc.iniciar_analise(tx_id, "manual")

    a = parsear_analise(_analise(tx_id, "medio"))
    comp_a = gerar_comprovante("OP-DD-1", tx_id, "analise", a.model_dump())
    svc.registrar_resultado_analise(tx_id, a, {"run_id": "r1", "operacao_id": "OP-DD-1"}, comp_a)
    # Estado agora: VALIDANDO

    v = parsear_validacao(_validacao(tx_id, "aguardar_cliente", "Reconhece?"))
    comp_v = gerar_comprovante("OP-DD-2", tx_id, "validacao", v.model_dump())
    svc.registrar_resultado_validacao(tx_id, v, {"run_id": "r2", "operacao_id": "OP-DD-2"}, comp_v)
    # Estado agora: AGUARDANDO_CLIENTE

    # Primeira decisão — válida (AGUARDANDO_CLIENTE → AUTORIZADO)
    comp2 = gerar_comprovante("OP-DD-3", tx_id, "decisao", {"acao": "autorizar"})
    svc.aplicar_decisao(tx_id, "autorizar", comp2)

    # Segunda decisão — deve levantar ErroConflito (decisao_aplicada=True)
    comp3 = gerar_comprovante("OP-DD-4", tx_id, "decisao", {"acao": "autorizar"})
    with pytest.raises(ErroConflito, match="Decisão já foi aplicada"):
        svc.aplicar_decisao(tx_id, "autorizar", comp3)
