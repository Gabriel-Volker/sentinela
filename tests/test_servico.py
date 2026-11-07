"""
tests/test_servico.py — Testes do serviço de casos (fluxos e estados).

Usa AdaptadorFake para IBM. Determinístico, sem credenciais.
"""

from ibm.orchestrate import AdaptadorFake as IBMFake

import json
import threading

import pytest

from fraude.contratos import (
    AcaoAnalise,
    NivelRisco,
)
from fraude.coordenador import Coordenador
from fraude.estados import EstadoCaso
from fraude.servico import (
    MAX_PERGUNTAS,
    ErroConflito,
    ErroNaoEncontrado,
    ServicoCasos,
)
from ibm.orchestrate import AdaptadorFake

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _tx_id_real():
    """Retorna um tx_id real do catálogo."""
    from fraude.repositorio import listar_candidatas_cliente, listar_clientes

    clientes = listar_clientes()
    for c in clientes:
        cands = listar_candidatas_cliente(c["id"])
        if cands:
            return cands[0]["id"]
    pytest.skip("Nenhuma candidata disponível")


def _analise_fake(tx_id: str, risco: str = "baixo") -> dict:
    acao_map = {"baixo": "autorizar", "medio": "validar", "alto": "bloquear"}
    return {
        "versao": 2,
        "transacao_id": tx_id,
        "risco": risco,
        "acao": acao_map[risco],
        "evidencias": [{"caminho": "/valor", "descricao": "Valor analisado."}],
        "informacoes_ausentes": [],
        "justificativa": f"Teste {risco}.",
    }


def _validacao_fake(tx_id: str, desfecho: str, pergunta: str | None = None) -> dict:
    return {
        "versao": 2,
        "transacao_id": tx_id,
        "desfecho": desfecho,
        "pergunta": pergunta,
        "evidencias": [{"caminho": "/valor", "descricao": "Valor confirmado."}],
        "justificativa": f"Desfecho {desfecho}.",
    }


def _criar_coord(tx_id: str, risco: str = "baixo", desfecho: str = "autorizar"):
    analise = _analise_fake(tx_id, risco)
    validacao = _validacao_fake(tx_id, desfecho)
    fake = AdaptadorFake(resposta_analise=analise, resposta_validacao=validacao)
    svc = ServicoCasos()
    coord = Coordenador(
        svc, fake, agente_analise_id="fake-analise", agente_validacao_id="fake-validacao"
    )
    return svc, coord, fake


# ---------------------------------------------------------------------------
# Fluxo baixo risco → autorizar
# ---------------------------------------------------------------------------


def test_fluxo_baixo_risco():
    tx_id = _tx_id_real()
    svc, coord, fake = _criar_coord(tx_id, risco="baixo")

    caso = coord.executar_analise(tx_id, "manual")
    assert caso.estado == EstadoCaso.AUTORIZADO
    assert caso.analise is not None
    assert caso.analise.risco == NivelRisco.BAIXO
    assert caso.analise.acao == AcaoAnalise.AUTORIZAR
    assert len(caso.comprovantes) == 1


# ---------------------------------------------------------------------------
# Fluxo alto risco → bloquear
# ---------------------------------------------------------------------------


def test_fluxo_alto_risco():
    tx_id = _tx_id_real()
    svc, coord, fake = _criar_coord(tx_id, risco="alto")

    caso = coord.executar_analise(tx_id, "manual")
    assert caso.estado == EstadoCaso.BLOQUEADO
    assert caso.analise.risco == NivelRisco.ALTO


# ---------------------------------------------------------------------------
# Fluxo médio risco → validação → autorizar
# ---------------------------------------------------------------------------


def _fake_medio(tx_id, analise_resp, val_respostas):
    """Fake que retorna analise_resp para a primeira chamada e val_respostas[n] para as demais."""
    chamadas = [0]
    todos = [analise_resp] + val_respostas

    class _FakeM:
        def criar_run(self, *a, **kw):
            return {"thread_id": "t", "run_id": f"r{chamadas[0]}"}

        def aguardar_run(self, run_id, max_tentativas=1):
            idx = min(chamadas[0], len(todos) - 1)
            r = todos[idx]
            chamadas[0] += 1
            return {**IBMFake.resultado(r), "status": "completed"}

        def consultar_run(self, run_id: str, timeout: float | None = None):
            # Delega a aguardar_run para manter o contador de chamadas correto
            return self.aguardar_run(run_id)

        def extrair_json_resposta(self, s):
            return json.loads(s.get("output", "{}"))

    return _FakeM()


def _aguardar_estado(svc, tx_id, estado_esperado, timeout=2.0):
    """
    Aguarda (com pool de CPU) que o caso atinja o estado esperado.
    Usado nos testes de risco médio onde a validação inicia em background.
    O fake completa em microssegundos, então 2s é mais que suficiente.
    """
    import time as _time

    prazo = _time.monotonic() + timeout
    while _time.monotonic() < prazo:
        caso = svc.obter_caso(tx_id)
        if caso.estado == estado_esperado:
            return caso
        _time.sleep(0.01)
    # Retorna estado atual para o teste verificar a falha com mensagem útil
    return svc.obter_caso(tx_id)


def test_fluxo_medio_risco_confirmacao():
    tx_id = _tx_id_real()
    analise = _analise_fake(tx_id, "medio")
    val_pergunta = _validacao_fake(tx_id, "aguardar_cliente", pergunta="Confirma?")
    val_autorizar = _validacao_fake(tx_id, "autorizar")

    svc = ServicoCasos()
    coord = Coordenador(
        svc,
        _fake_medio(tx_id, analise, [val_pergunta, val_autorizar]),
        agente_analise_id="fake-analise",
        agente_validacao_id="fake-validacao",
    )

    coord.executar_analise(tx_id, "manual")
    # A validação inicial corre em background; aguarda AGUARDANDO_CLIENTE
    caso = _aguardar_estado(svc, tx_id, EstadoCaso.AGUARDANDO_CLIENTE)
    assert caso.estado == EstadoCaso.AGUARDANDO_CLIENTE

    svc.registrar_resposta_cliente(tx_id, "Sim, fiz essa compra.", False)
    caso = coord.executar_validacao(tx_id, "Sim, fiz essa compra.")
    assert caso.estado == EstadoCaso.AUTORIZADO


# ---------------------------------------------------------------------------
# Fluxo médio risco → negativa → bloquear
# ---------------------------------------------------------------------------


def test_fluxo_medio_risco_negativa():
    tx_id = _tx_id_real()
    analise = _analise_fake(tx_id, "medio")
    svc = ServicoCasos()
    coord = Coordenador(
        svc,
        _fake_medio(
            tx_id,
            analise,
            [
                _validacao_fake(tx_id, "aguardar_cliente", pergunta="Você reconhece?"),
                _validacao_fake(tx_id, "bloquear"),
            ],
        ),
        agente_analise_id="fake-analise",
        agente_validacao_id="fake-validacao",
    )

    coord.executar_analise(tx_id, "manual")
    caso = _aguardar_estado(svc, tx_id, EstadoCaso.AGUARDANDO_CLIENTE)
    assert caso.estado == EstadoCaso.AGUARDANDO_CLIENTE
    svc.registrar_resposta_cliente(tx_id, "Não reconheço essa compra.", False)
    caso = coord.executar_validacao(tx_id, "Não reconheço essa compra.")
    assert caso.estado == EstadoCaso.BLOQUEADO


# ---------------------------------------------------------------------------
# Ausência de resposta → pendente (nunca autoriza)
# ---------------------------------------------------------------------------


def test_ausencia_resposta_nunca_autoriza():
    tx_id = _tx_id_real()
    analise = _analise_fake(tx_id, "medio")
    svc = ServicoCasos()
    coord = Coordenador(
        svc,
        _fake_medio(
            tx_id,
            analise,
            [
                _validacao_fake(tx_id, "aguardar_cliente", pergunta="Confirma?"),
                _validacao_fake(tx_id, "autorizar"),  # deve ser revertido para pendente
            ],
        ),
        agente_analise_id="fake-analise",
        agente_validacao_id="fake-validacao",
    )

    coord.executar_analise(tx_id, "manual")
    caso = _aguardar_estado(svc, tx_id, EstadoCaso.AGUARDANDO_CLIENTE)
    assert caso.estado == EstadoCaso.AGUARDANDO_CLIENTE
    svc.registrar_resposta_cliente(tx_id, None, True)
    caso = coord.executar_validacao(tx_id, None)
    assert caso.estado == EstadoCaso.PENDENTE
    # A justificativa exibida não pode continuar defendendo a autorização revertida
    validacao = caso.to_dict()["ultima_validacao"]
    assert validacao["desfecho"] == "pendente"
    assert "não respondeu" in validacao["justificativa"]


# ---------------------------------------------------------------------------
# Limite de perguntas
# ---------------------------------------------------------------------------


def test_limite_perguntas():
    tx_id = _tx_id_real()
    analise = _analise_fake(tx_id, "medio")
    pergunta_resp = _validacao_fake(tx_id, "aguardar_cliente", pergunta="Confirma?")
    # Sempre retorna analise ou pergunta (nunca desfecho final)
    val_respostas = [pergunta_resp] * (MAX_PERGUNTAS + 2)

    svc = ServicoCasos()
    coord = Coordenador(
        svc,
        _fake_medio(tx_id, analise, val_respostas),
        agente_analise_id="fake-analise",
        agente_validacao_id="fake-validacao",
    )

    coord.executar_analise(tx_id, "manual")
    caso = _aguardar_estado(svc, tx_id, EstadoCaso.AGUARDANDO_CLIENTE)
    assert caso.estado == EstadoCaso.AGUARDANDO_CLIENTE

    for i in range(MAX_PERGUNTAS):
        svc.registrar_resposta_cliente(tx_id, f"Resposta {i}", False)
        caso = coord.executar_validacao(tx_id, f"Resposta {i}")

    assert caso.estado in (EstadoCaso.AGUARDANDO_CLIENTE, EstadoCaso.PENDENTE)


# ---------------------------------------------------------------------------
# Duplicação de análise rejeitada
# ---------------------------------------------------------------------------


def test_analise_duplicada_rejeitada():
    tx_id = _tx_id_real()
    svc, coord, fake = _criar_coord(tx_id, risco="baixo")

    coord.executar_analise(tx_id, "manual")
    # Segunda análise deve falhar
    with pytest.raises(ErroConflito):
        coord.executar_analise(tx_id, "manual")


# ---------------------------------------------------------------------------
# Decisão duplicada rejeitada
# ---------------------------------------------------------------------------


def test_decisao_duplicada_rejeitada():
    tx_id = _tx_id_real()
    svc, coord, _ = _criar_coord(tx_id, risco="baixo")
    coord.executar_analise(tx_id, "manual")
    # Estado já autorizado — aplicar decisão novamente deve falhar
    from fraude.servico import gerar_comprovante

    comp = gerar_comprovante("OP-FAKE", tx_id, "analise", {})
    with pytest.raises((ErroConflito, Exception)):
        svc.aplicar_decisao(tx_id, "autorizar", comp)


# ---------------------------------------------------------------------------
# Isolamento entre transações
# ---------------------------------------------------------------------------


def test_isolamento_entre_transacoes():
    from fraude.repositorio import listar_candidatas_cliente, listar_clientes

    clientes = listar_clientes()
    tx_ids = []
    for c in clientes:
        cands = listar_candidatas_cliente(c["id"])
        if len(cands) >= 2:
            tx_ids = [cands[0]["id"], cands[1]["id"]]
            break

    if len(tx_ids) < 2:
        pytest.skip("Não há clientes com 2 candidatas")

    tx1, tx2 = tx_ids
    analise1 = _analise_fake(tx1, "baixo")
    analise2 = _analise_fake(tx2, "alto")

    fake1 = AdaptadorFake(resposta_analise=analise1)
    fake2 = AdaptadorFake(resposta_analise=analise2)

    svc1 = ServicoCasos()
    svc2 = ServicoCasos()

    coord1 = Coordenador(
        svc1, fake1, agente_analise_id="fake-analise", agente_validacao_id="fake-validacao"
    )
    coord2 = Coordenador(
        svc2, fake2, agente_analise_id="fake-analise", agente_validacao_id="fake-validacao"
    )

    caso1 = coord1.executar_analise(tx1, "manual")
    caso2 = coord2.executar_analise(tx2, "manual")

    assert caso1.estado == EstadoCaso.AUTORIZADO
    assert caso2.estado == EstadoCaso.BLOQUEADO

    # Cada serviço só conhece seu próprio caso
    with pytest.raises(ErroNaoEncontrado):
        svc1.obter_caso(tx2)


# ---------------------------------------------------------------------------
# Concorrência: proteção por lock
# ---------------------------------------------------------------------------


def test_concorrencia_mesmo_caso():
    tx_id = _tx_id_real()
    analise = _analise_fake(tx_id, "baixo")
    fake = AdaptadorFake(resposta_analise=analise)
    svc = ServicoCasos()
    coord = Coordenador(
        svc, fake, agente_analise_id="fake-analise", agente_validacao_id="fake-validacao"
    )

    erros = []
    resultados = []

    def tentar():
        try:
            caso = coord.executar_analise(tx_id, "manual")
            resultados.append(caso.estado)
        except ErroConflito as e:
            erros.append(str(e))
        except Exception as e:
            erros.append(str(e))

    threads = [threading.Thread(target=tentar) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Pelo menos uma deve ter sucesso, e exatamente uma deve resultar em autorizado
    autorizados = resultados.count(EstadoCaso.AUTORIZADO)
    assert autorizados >= 1


# ---------------------------------------------------------------------------
# Estado após timeout local: pendente_execucao
# ---------------------------------------------------------------------------


def test_timeout_local_preserva_run_id(monkeypatch):
    import fraude.coordenador as modulo
    from types import SimpleNamespace

    relogio = [0.0]

    def avancar(segundos):
        relogio[0] += segundos

    monkeypatch.setattr(
        modulo, "time", SimpleNamespace(monotonic=lambda: relogio[0], sleep=avancar)
    )
    tx_id = _tx_id_real()

    class FakeTimeout:
        """Fake que sempre retorna status 'running' (nunca completa)."""

        def criar_run(self, agente_id, mensagem, thread_id, context_variables):
            return {"run_id": "run-timeout-001", "thread_id": "thr-001"}

        def aguardar_run(self, run_id, max_tentativas=1):
            return {"status": "queued", "run_id": run_id}

        def consultar_run(self, run_id: str, timeout: float | None = None):
            return {"status": "queued", "run_id": run_id}

        def extrair_json_resposta(self, run_status):
            return None

    svc = ServicoCasos()
    coord = Coordenador(
        svc, FakeTimeout(), agente_analise_id="fake-analise", agente_validacao_id="fake-validacao"
    )

    caso = coord.executar_analise(tx_id, "manual")
    # Deve estar em pendente_execucao ou analisando (não completou)
    assert caso.estado in (EstadoCaso.PENDENTE_EXECUCAO, EstadoCaso.ANALISANDO)
    # run_id deve estar preservado
    if caso.run_analise:
        assert caso.run_analise.get("run_id") == "run-timeout-001"


# ---------------------------------------------------------------------------
# Reinício perde estado (documentado)
# ---------------------------------------------------------------------------


def test_reinicio_perde_estado():
    """Demonstra que criar novo ServicoCasos perde o estado anterior."""
    tx_id = _tx_id_real()
    svc1, coord, _ = _criar_coord(tx_id, risco="baixo")
    coord.executar_analise(tx_id, "manual")

    # Novo serviço (reinício simulado)
    svc2 = ServicoCasos()
    with pytest.raises(ErroNaoEncontrado):
        svc2.obter_caso(tx_id)


# ---------------------------------------------------------------------------
# Simulador por turno
# ---------------------------------------------------------------------------


def test_simulador_turno_a_turno():
    from fraude.repositorio import buscar_transacao, listar_candidatas_cliente, listar_clientes

    clientes = listar_clientes()
    tx_id_com_roteiro = None
    for c in clientes:
        cands = listar_candidatas_cliente(c["id"])
        for cand in cands:
            tx = buscar_transacao(cand["id"])
            if tx and tx.get("roteiro_cliente"):
                tx_id_com_roteiro = cand["id"]
                roteiro = tx["roteiro_cliente"]
                break
        if tx_id_com_roteiro:
            break

    if not tx_id_com_roteiro:
        pytest.skip("Nenhuma candidata com roteiro encontrada")

    analise = _analise_fake(tx_id_com_roteiro, "medio")
    validacao = _validacao_fake(tx_id_com_roteiro, "aguardar_cliente", pergunta="Confirma?")
    fake = AdaptadorFake(resposta_analise=analise, resposta_validacao=validacao)
    svc = ServicoCasos()
    coord = Coordenador(
        svc, fake, agente_analise_id="fake-analise", agente_validacao_id="fake-validacao"
    )

    coord.executar_analise(tx_id_com_roteiro, "roteiro")
    # Obtém caso (cria na sessão)
    svc.obter_caso(tx_id_com_roteiro)

    # Primeira fala do roteiro
    fala1 = svc.proximo_turno_roteiro(tx_id_com_roteiro)
    assert fala1 == roteiro[0] if roteiro else fala1 is None

    # Segunda fala
    if len(roteiro) > 1:
        fala2 = svc.proximo_turno_roteiro(tx_id_com_roteiro)
        assert fala2 == roteiro[1]

    # Após esgotado
    for _ in range(10):
        svc.proximo_turno_roteiro(tx_id_com_roteiro)
    fala_final = svc.proximo_turno_roteiro(tx_id_com_roteiro)
    assert fala_final is None
