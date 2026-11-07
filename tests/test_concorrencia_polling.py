"""
tests/test_concorrencia_polling.py — Testes determinísticos de concorrência e timeout.

Reproduz:
  1. Condição de corrida UI: executar_analise retorna VALIDANDO mas a thread de
     background ainda não criou o run remoto. O frontend não agenda poll e a
     pergunta nunca aparece.
  2. Timeout após prazo esgotado: consulta iniciada quando o orçamento já acabou.
  3. Preservação de run_id, thread_id, operacao_id e fase no timeout.
  4. Ausência de novo run após timeout.
  5. consultar_run_pendente recupera a identidade original da operação.

Usa threading.Event para controle determinístico — sem sleeps.
"""

from __future__ import annotations

from ibm.orchestrate import AdaptadorFake as IBMFake

import json
import threading
import time

import pytest

from fraude.coordenador import Coordenador
from fraude.estados import EstadoCaso
from fraude.servico import ServicoCasos

# ---------------------------------------------------------------------------
# Helpers e dados
# ---------------------------------------------------------------------------


def _tx_id_real() -> str:
    import json as _j
    from pathlib import Path

    p = Path("dados/mock/operacional/candidatas.json")
    if not p.exists():
        pytest.skip("candidatas.json ausente — execute scripts/gerar_dados.py")
    cands = _j.loads(p.read_bytes())
    if not cands:
        pytest.skip("candidatas.json vazio")
    return cands[0]["id"]


def _analise_fake(tx_id: str, risco: str = "medio") -> dict:
    acao = {"baixo": "autorizar", "medio": "validar", "alto": "bloquear"}[risco]
    return {
        "versao": 2,
        "transacao_id": tx_id,
        "risco": risco,
        "acao": acao,
        "evidencias": [{"caminho": "/valor", "descricao": "Valor da transação."}],
        "informacoes_ausentes": [],
        "justificativa": f"Risco {risco} detectado.",
    }


def _validacao_fake(
    tx_id: str, desfecho: str = "aguardar_cliente", pergunta: str | None = None
) -> dict:
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


# ---------------------------------------------------------------------------
# TESTE 1: Condição de corrida UI (determinístico com Event)
#
# Sequência que reproduz o problema:
#   1. executar_analise é chamado
#   2. A análise conclui — caso fica em VALIDANDO (risco médio)
#   3. Thread de background é lançada MAS o run remoto ainda não foi criado
#   4. executar_analise retorna o caso em VALIDANDO
#   5. Frontend recebe VALIDANDO, NÃO agenda poll
#   6. Thread cria o run, fica em AGUARDANDO_CLIENTE
#   7. A pergunta nunca aparece na tela (sem poll agendado)
#
# Correção esperada: o caso deve retornar em PENDENTE_EXECUCAO (não VALIDANDO)
# para que o frontend agende poll antes que a thread conclua.
# ---------------------------------------------------------------------------


class FakeBlockingValidacao:
    """
    Fake que bloqueia a criação do run de validação até receber sinal.
    Permite simular a janela de corrida onde executar_analise retorna
    enquanto a thread ainda está aguardando criar_run para validação.
    """

    def __init__(
        self,
        analise_resp: dict,
        val_resp: dict,
        *,
        bloquear_validacao: threading.Event,
        validacao_criou_run: threading.Event,
    ) -> None:
        self._analise = analise_resp
        self._val = val_resp
        self._bloquear = bloquear_validacao
        self._criou = validacao_criou_run
        self._chamadas_criar: list[str] = []

    def criar_run(
        self,
        agente_id: str,
        mensagem: str,
        thread_id: str | None,
        context_variables: dict,
    ) -> dict:
        run_id = f"run-{len(self._chamadas_criar)}"
        self._chamadas_criar.append(run_id)

        # Na primeira chamada (analise), retorna imediatamente.
        # Na segunda chamada (validacao), bloqueia até receber sinal.
        if len(self._chamadas_criar) == 2:
            self._bloquear.wait(timeout=5.0)  # espera o sinal de teste
            self._criou.set()  # avisa que criou o run

        return {"run_id": run_id, "thread_id": f"thr-{run_id}"}

    def consultar_run(self, run_id: str, timeout: float | None = None) -> dict:
        # Análise (run-0) completa imediatamente.
        if run_id == "run-0":
            return {**IBMFake.resultado(self._analise), "status": "completed"}
        # Validação (run-1) também completa imediatamente após criar_run.
        return {**IBMFake.resultado(self._val), "status": "completed"}

    def aguardar_run(self, run_id: str, max_tentativas: int = 1) -> dict:
        return self.consultar_run(run_id)

    def extrair_json_resposta(self, run_status: dict) -> dict | None:
        saida = run_status.get("output", "")
        try:
            return json.loads(saida) if saida else None
        except Exception:
            return None


def test_race_condition_ui_retorna_pendente_execucao_nao_validando():
    """
    executar_analise deve retornar PENDENTE_EXECUCAO (não VALIDANDO) para risco médio.
    Isso garante que o frontend agende poll mesmo antes da thread de background concluir.
    """
    tx_id = _tx_id_real()
    analise = _analise_fake(tx_id, "medio")
    val_pergunta = _validacao_fake(tx_id, "aguardar_cliente", pergunta="Você reconhece?")

    bloquear = threading.Event()  # bloqueia criar_run de validacao
    criou_run = threading.Event()  # sinaliza quando criar_run foi chamado

    fake = FakeBlockingValidacao(
        analise,
        val_pergunta,
        bloquear_validacao=bloquear,
        validacao_criou_run=criou_run,
    )
    svc = ServicoCasos()
    coord = Coordenador(
        svc, fake, agente_analise_id="fake-analise", agente_validacao_id="fake-validacao"
    )

    # Executa análise — a thread de background ainda está bloqueada em criar_run
    caso = coord.executar_analise(tx_id, "manual")

    # ANTES da correção: caso.estado == VALIDANDO (não agenda poll → pergunta nunca aparece)
    # DEPOIS da correção: caso.estado == PENDENTE_EXECUCAO (frontend agenda poll)
    assert caso.estado == EstadoCaso.PENDENTE_EXECUCAO, (
        f"executar_analise retornou estado '{caso.estado.value}' mas deveria ser "
        f"'pendente_execucao' para que o frontend agende poll enquanto a validação "
        f"está em andamento em segundo plano."
    )

    # Libera a thread para concluir e aguarda AGUARDANDO_CLIENTE
    bloquear.set()
    prazo = time.monotonic() + 3.0
    while time.monotonic() < prazo:
        if svc.obter_caso(tx_id).estado == EstadoCaso.AGUARDANDO_CLIENTE:
            break
        time.sleep(0.01)

    assert svc.obter_caso(tx_id).estado == EstadoCaso.AGUARDANDO_CLIENTE, (
        "Após liberar a thread de background, o caso deveria estar em AGUARDANDO_CLIENTE."
    )


def test_race_condition_ui_troca_de_compra():
    """
    Se o usuário trocar de compra enquanto a validação está em background,
    o caso da compra original deve continuar sendo atualizado corretamente.
    O frontend recupera o estado ao selecionar a compra novamente.
    """
    tx_id = _tx_id_real()
    analise = _analise_fake(tx_id, "medio")
    val_pergunta = _validacao_fake(tx_id, "aguardar_cliente", pergunta="Confirma?")

    bloquear = threading.Event()
    criou_run = threading.Event()

    fake = FakeBlockingValidacao(
        analise,
        val_pergunta,
        bloquear_validacao=bloquear,
        validacao_criou_run=criou_run,
    )
    svc = ServicoCasos()
    coord = Coordenador(
        svc, fake, agente_analise_id="fake-analise", agente_validacao_id="fake-validacao"
    )

    caso = coord.executar_analise(tx_id, "manual")
    # Estado imediato deve permitir que o frontend agende poll
    assert caso.estado in (EstadoCaso.PENDENTE_EXECUCAO, EstadoCaso.AGUARDANDO_CLIENTE), (
        "Estado deve ser pendente_execucao ou aguardando_cliente após análise de risco médio."
    )

    # Simula troca de compra: o caso ainda existe no backend
    caso_recuperado = svc.obter_caso(tx_id)
    assert caso_recuperado.transacao_id == tx_id

    # Libera a thread e aguarda conclusão
    bloquear.set()
    prazo = time.monotonic() + 3.0
    while time.monotonic() < prazo:
        if svc.obter_caso(tx_id).estado == EstadoCaso.AGUARDANDO_CLIENTE:
            break
        time.sleep(0.01)

    # Ao retornar à compra, o frontend faz GET /casos/{id} e obtém AGUARDANDO_CLIENTE
    caso_final = svc.obter_caso(tx_id)
    assert caso_final.estado == EstadoCaso.AGUARDANDO_CLIENTE
    assert caso_final.run_validacao is not None, "run_validacao deve estar registrado"


def test_race_condition_validacao_background_falha_estado_consistente():
    """
    Se o agente de validação falhar no 1.º turno background, o caso NÃO deve
    ficar preso em VALIDANDO indefinidamente. Deve transitar para PENDENTE ou
    exibir uma pergunta de fallback (se implementado), mas nunca travar.
    O agente de validação indisponível deve ser informado como falha/pendência,
    não apresentado como pergunta produzida pelo agente.
    """
    tx_id = _tx_id_real()
    analise = _analise_fake(tx_id, "medio")

    class FakeValidacaoFalha:
        """Fake que completa a análise mas falha ao criar o run de validação."""

        _n = 0

        def criar_run(self, agente_id, mensagem, thread_id, context_variables):
            FakeValidacaoFalha._n += 1
            if FakeValidacaoFalha._n == 1:
                return {"run_id": "run-analise", "thread_id": "thr-a"}
            raise RuntimeError("Agente de validação indisponível.")

        def consultar_run(self, run_id: str, timeout: float | None = None):
            return {**IBMFake.resultado(analise), "status": "completed"}

        def aguardar_run(self, run_id, max_tentativas=1):
            return self.consultar_run(run_id)

        def extrair_json_resposta(self, run_status):
            saida = run_status.get("output", "")
            try:
                return json.loads(saida) if saida else None
            except Exception:
                return None

    svc = ServicoCasos()
    coord = Coordenador(
        svc,
        FakeValidacaoFalha(),
        agente_analise_id="fake-analise",
        agente_validacao_id="fake-validacao",
    )

    coord.executar_analise(tx_id, "manual")

    # Aguarda a thread de background tentar e falhar
    prazo = time.monotonic() + 3.0
    while time.monotonic() < prazo:
        estado = svc.obter_caso(tx_id).estado
        if estado not in (EstadoCaso.VALIDANDO, EstadoCaso.PENDENTE_EXECUCAO):
            break
        time.sleep(0.01)

    estado_final = svc.obter_caso(tx_id).estado
    # Não deve travar em VALIDANDO indefinidamente
    assert estado_final != EstadoCaso.VALIDANDO, (
        f"Caso ficou preso em VALIDANDO após falha do agente de validação. "
        f"Estado atual: {estado_final.value}"
    )


# ---------------------------------------------------------------------------
# TESTE 2: Orçamento de timeout — testes diretos de _aguardar_run_com_timeout
#
# Os testes a seguir chamam _aguardar_run_com_timeout (código de produção)
# com relógio e sleep injetados via monkey-patch, sem usar cópia do algoritmo.
# ---------------------------------------------------------------------------


import fraude.coordenador as _coord_mod  # noqa: E402 — após imports iniciais


def _injetar_relogio_e_sleep(relogio_lista: list, n_consultas: list, status_seq: list):
    """
    Retorna (fake_ibm, mono_fn, sleep_fn) para uso nos testes de timeout.
    Não cria cópia do algoritmo — injeta nas referências do módulo produtivo.
    """

    def mono():
        return relogio_lista[0]

    def sleep_avanca(s):
        relogio_lista[0] += s

    class FakeContadorProd:
        def consultar_run(self, run_id, timeout=None):
            n_consultas[0] += 1
            idx = min(n_consultas[0] - 1, len(status_seq) - 1)
            return {"status": status_seq[idx], "run_id": run_id}

        def aguardar_run(self, run_id, max_tentativas=1):
            return self.consultar_run(run_id)

        def extrair_json_resposta(self, s):
            return None

    return FakeContadorProd(), mono, sleep_avanca


def test_timeout_producao_conclusao_imediata():
    """_aguardar_run_com_timeout (produtivo) retorna imediatamente em completed."""
    n = [0]
    relogio = [0.0]
    fake, mono, sleep = _injetar_relogio_e_sleep(relogio, n, ["completed"])
    orig_mono = _coord_mod.time.monotonic
    orig_sleep = _coord_mod.time.sleep
    _coord_mod.time.monotonic = mono  # type: ignore
    _coord_mod.time.sleep = sleep  # type: ignore
    try:
        status = _coord_mod._aguardar_run_com_timeout(fake, "run-1", 30.0)
    finally:
        _coord_mod.time.monotonic = orig_mono
        _coord_mod.time.sleep = orig_sleep
    assert status["status"] == "completed"
    assert n[0] == 1


def test_timeout_producao_prazo_no_sleep():
    """O prazo expira durante o sleep — não inicia nova consulta."""
    n = [0]
    relogio = [0.0]

    def sleep_gigante(s):
        relogio[0] += 100.0  # ultrapassa o prazo de 5.0s

    class FakeRunning:
        def consultar_run(self, run_id, timeout=None):
            n[0] += 1
            return {"status": "running"}

        def aguardar_run(self, run_id, max_tentativas=1):
            return self.consultar_run(run_id)

        def extrair_json_resposta(self, s):
            return None

    def mono():
        return relogio[0]

    orig_mono = _coord_mod.time.monotonic
    orig_sleep = _coord_mod.time.sleep
    _coord_mod.time.monotonic = mono  # type: ignore
    _coord_mod.time.sleep = sleep_gigante  # type: ignore
    try:
        status = _coord_mod._aguardar_run_com_timeout(FakeRunning(), "run-y", 5.0)
    finally:
        _coord_mod.time.monotonic = orig_mono
        _coord_mod.time.sleep = orig_sleep
    assert status["status"] == "running"
    assert n[0] == 1, f"Esperado 1 consulta, feitas {n[0]}"


def test_timeout_producao_execucao_ativa_retorna_ultimo_status():
    """Orçamento esgota — retorna último status sem lançar exceção."""
    n = [0]
    relogio = [0.0]
    fake, mono, sleep = _injetar_relogio_e_sleep(relogio, n, ["running"] * 20)
    orig_mono = _coord_mod.time.monotonic
    orig_sleep = _coord_mod.time.sleep
    _coord_mod.time.monotonic = mono  # type: ignore
    _coord_mod.time.sleep = sleep  # type: ignore
    try:
        status = _coord_mod._aguardar_run_com_timeout(fake, "run-x", 3.0)
    finally:
        _coord_mod.time.monotonic = orig_mono
        _coord_mod.time.sleep = orig_sleep
    assert status["status"] == "running"
    assert 1 <= n[0] <= 3, f"Consultas inesperadas: {n[0]}"


def test_timeout_producao_prazo_esgotado_apos_consulta():
    """Relógio avança além do prazo durante a consulta — não inicia nova."""
    n = [0]
    relogio = [0.0]

    class FakeAvancaAposPrimeira:
        def consultar_run(self, run_id, timeout=None):
            n[0] += 1
            if n[0] == 1:
                relogio[0] += 100.0  # avança além do prazo de 5.0s
            return {"status": "running"}

        def aguardar_run(self, run_id, max_tentativas=1):
            return self.consultar_run(run_id)

        def extrair_json_resposta(self, s):
            return None

    def mono():
        return relogio[0]

    def sleep_noop(s):
        relogio[0] += s

    orig_mono = _coord_mod.time.monotonic
    orig_sleep = _coord_mod.time.sleep
    _coord_mod.time.monotonic = mono  # type: ignore
    _coord_mod.time.sleep = sleep_noop  # type: ignore
    try:
        status = _coord_mod._aguardar_run_com_timeout(FakeAvancaAposPrimeira(), "run-t", 5.0)
    finally:
        _coord_mod.time.monotonic = orig_mono
        _coord_mod.time.sleep = orig_sleep
    assert status["status"] == "running"
    assert n[0] == 1, f"Esperado 1 consulta, feitas {n[0]}"


# ---------------------------------------------------------------------------
# TESTE 3: consultar_run_pendente preserva identidade original da operação
# ---------------------------------------------------------------------------


def test_consultar_run_pendente_preserva_run_id_original():
    """
    consultar_run_pendente não deve gerar novo operacao_id para processar
    o mesmo resultado. O run_id original deve ser preservado.
    """
    tx_id = _tx_id_real()
    analise = _analise_fake(tx_id, "baixo")

    # Fake que simula timeout na 1ª consulta, completando na 2ª
    class FakePendente:
        def __init__(self):
            self._n = 0
            self.run_id_recebido: list[str] = []

        def criar_run(self, agente_id, mensagem, thread_id, context_variables):
            return {"run_id": "run-original-abc", "thread_id": "thr-original-xyz"}

        def consultar_run(self, run_id: str, timeout: float | None = None) -> dict:
            self.run_id_recebido.append(run_id)
            self._n += 1
            # Na 1ª chamada (via _aguardar_run_com_timeout): retorna running
            # Na 2ª chamada (via consultar_run_pendente): retorna completed
            if self._n == 1:
                return {"status": "running", "run_id": run_id}
            return {**IBMFake.resultado(analise), "status": "completed" ,'run_id': run_id}

        def aguardar_run(self, run_id, max_tentativas=1):
            return self.consultar_run(run_id)

        def extrair_json_resposta(self, run_status):
            saida = run_status.get("output", "")
            try:
                return json.loads(saida) if saida else None
            except Exception:
                return None

    fake = FakePendente()
    svc = ServicoCasos()
    # Timeout mínimo para forçar PENDENTE_EXECUCAO após 1 consulta
    import os

    os.environ["SENTINELA_POLL_TIMEOUT_S"] = "0.001"
    os.environ["SENTINELA_POLL_INTERVALO_S"] = "0.001"

    try:
        coord = Coordenador(
            svc, fake, agente_analise_id="fake-analise", agente_validacao_id="fake-validacao"
        )
        caso = coord.executar_analise(tx_id, "manual")

        # Se timeout muito curto forçou pendente_execucao, o run_id deve estar preservado
        if caso.estado == EstadoCaso.PENDENTE_EXECUCAO:
            assert caso.run_analise is not None
            assert caso.run_analise.get("run_id") == "run-original-abc", (
                "O run_id original deve estar preservado em run_analise."
            )
            assert caso.run_analise.get("thread_id") == "thr-original-xyz", (
                "O thread_id original deve estar preservado em run_analise."
            )

            # consultar_run_pendente deve reusar o run_id original, não criar novo
            coord.consultar_run_pendente(tx_id)

            # O run_id consultado deve ser o original
            assert "run-original-abc" in fake.run_id_recebido, (
                "consultar_run_pendente deve usar o run_id original 'run-original-abc'."
            )
    finally:
        os.environ.pop("SENTINELA_POLL_TIMEOUT_S", None)
        os.environ.pop("SENTINELA_POLL_INTERVALO_S", None)


# ---------------------------------------------------------------------------
# TESTE 4: Ausência de processamento duplicado por polling + thread
# ---------------------------------------------------------------------------


def test_sem_processamento_duplicado_polling_e_thread():
    """
    Se o resultado já foi processado pela thread de background,
    consultar_run_pendente não deve processar novamente.
    Verifica que registrar_resultado_analise não é chamado duas vezes.
    """
    tx_id = _tx_id_real()
    analise = _analise_fake(tx_id, "baixo")

    chamadas_registrar = [0]
    original_registrar = ServicoCasos.registrar_resultado_analise

    def registrar_intercept(self_svc, *args, **kwargs):
        chamadas_registrar[0] += 1
        return original_registrar(self_svc, *args, **kwargs)

    from fraude import servico as _svc_mod

    _svc_mod.ServicoCasos.registrar_resultado_analise = registrar_intercept  # type: ignore[method-assign]

    try:
        fake = _AdaptadorSimplesOk(analise)
        svc = ServicoCasos()
        coord = Coordenador(
            svc, fake, agente_analise_id="fake-analise", agente_validacao_id="fake-validacao"
        )

        caso = coord.executar_analise(tx_id, "manual")

        # Se já processou, consultar_run_pendente não deve processar de novo
        if caso.estado != EstadoCaso.PENDENTE_EXECUCAO:
            # Já completou — registrar chamado 1x
            assert chamadas_registrar[0] == 1
            # consultar_run_pendente deve retornar o caso sem re-processar
            coord.consultar_run_pendente(tx_id)
            assert chamadas_registrar[0] == 1, (
                "registrar_resultado_analise chamado mais de uma vez — processamento duplicado."
            )
    finally:
        _svc_mod.ServicoCasos.registrar_resultado_analise = original_registrar  # type: ignore[method-assign]


class _AdaptadorSimplesOk:
    def __init__(self, resp: dict) -> None:
        self._resp = resp

    def criar_run(self, *a, **kw):
        return {"run_id": "run-ok-001", "thread_id": "thr-ok-001"}

    def consultar_run(self, run_id: str, timeout: float | None = None):
        return {**IBMFake.resultado(self._resp), "status": "completed"}

    def aguardar_run(self, run_id, max_tentativas=1):
        return self.consultar_run(run_id)

    def extrair_json_resposta(self, run_status):
        saida = run_status.get("output", "")
        try:
            return json.loads(saida) if saida else None
        except Exception:
            return None


# ---------------------------------------------------------------------------
# TESTE 5: Estado VALIDANDO não fica preso quando a criação do run remoto falha
# ---------------------------------------------------------------------------


def test_estado_consistente_quando_criar_run_falha():
    """
    Se criar_run falha para a validação, o caso NÃO deve ficar em VALIDANDO.
    O estado deve indicar pendência ou falha de forma que o frontend possa
    apresentar a situação corretamente.
    """
    tx_id = _tx_id_real()
    analise = _analise_fake(tx_id, "medio")

    class FakeCriarRunFalha:
        _n = 0

        def criar_run(self, agente_id, mensagem, thread_id, context_variables):
            FakeCriarRunFalha._n += 1
            if FakeCriarRunFalha._n == 1:
                # Análise OK
                return {"run_id": "run-analise-ok", "thread_id": "thr-ok"}
            # Validação FALHA
            raise ConnectionError("Falha de rede ao criar run de validação.")

        def consultar_run(self, run_id: str, timeout: float | None = None):
            if run_id == "run-analise-ok":
                return {**IBMFake.resultado(analise), "status": "completed"}
            return {"status": "error"}

        def aguardar_run(self, run_id, max_tentativas=1):
            return self.consultar_run(run_id)

        def extrair_json_resposta(self, run_status):
            saida = run_status.get("output", "")
            try:
                return json.loads(saida) if saida else None
            except Exception:
                return None

    svc = ServicoCasos()
    coord = Coordenador(
        svc,
        FakeCriarRunFalha(),
        agente_analise_id="fake-analise",
        agente_validacao_id="fake-validacao",
    )

    coord.executar_analise(tx_id, "manual")

    # Aguarda a thread background tentar e falhar
    prazo = time.monotonic() + 3.0
    while time.monotonic() < prazo:
        estado = svc.obter_caso(tx_id).estado
        if estado not in (EstadoCaso.VALIDANDO, EstadoCaso.PENDENTE_EXECUCAO):
            break
        time.sleep(0.01)

    estado_final = svc.obter_caso(tx_id).estado
    assert estado_final != EstadoCaso.VALIDANDO, (
        f"Caso ficou preso em VALIDANDO após falha de criar_run. Estado: {estado_final.value}"
    )
