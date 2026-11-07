"""Regressões com o coordenador e o serviço reais; somente a IBM é substituída."""

import json
import threading

import pytest

from fraude.contratos import parsear_analise, parsear_validacao
from fraude.coordenador import Coordenador
from fraude.estados import EstadoCaso
from fraude.repositorio import listar_candidatas_cliente, listar_clientes
from fraude.servico import ServicoCasos, gerar_comprovante
from ibm.orchestrate import extrair_json_resposta
from ibm.orchestrate import AdaptadorFake


def dados(fase, tx):
    base = {
        "versao": 2,
        "transacao_id": tx,
        "evidencias": [{"caminho": "/valor", "descricao": "Valor informado."}],
        "justificativa": "Teste de integração local.",
    }
    if fase == "analise":
        return {**base, "risco": "baixo", "acao": "autorizar", "informacoes_ausentes": []}
    return {**base, "desfecho": "aguardar_cliente", "pergunta": "Reconhece a compra?"}


@pytest.fixture
def cenario():
    tx = listar_candidatas_cliente(listar_clientes()[0]["id"])[0]["id"]
    svc = ServicoCasos()
    svc.iniciar_analise(tx, "manual")
    analise = {**dados("analise", tx), "risco": "medio", "acao": "validar"}
    svc.registrar_resultado_analise(
        tx, parsear_analise(analise), {}, gerar_comprovante("a", tx, "analise", analise)
    )
    svc.registrar_run_pendente(
        tx,
        {"run_id": "v1", "thread_id": "t", "operacao_id": "op1", "agente": "validacao"},
        "validacao",
    )
    return tx, svc


def test_poll_antigo_em_transito_nao_substitui_run_em_criacao(cenario):
    tx, svc = cenario
    entrou = threading.Event()
    liberar = threading.Event()
    erros = []

    class IBM:
        def consultar_run(self, *args, **kwargs):
            entrou.set()
            assert liberar.wait(3)
            return {"status": "completed", "output": json.dumps(dados("validacao", tx))}

        extrair_json_resposta = staticmethod(extrair_json_resposta)

    coord = Coordenador(svc, IBM())

    def poll():
        try:
            coord.consultar_run_pendente(tx)
        except Exception as exc:
            erros.append(exc)

    worker = threading.Thread(target=poll)
    worker.start()
    try:
        assert entrou.wait(3)
        v = dados("validacao", tx)
        svc.registrar_resultado_validacao(
            tx,
            parsear_validacao(v),
            {"run_id": "v1", "operacao_id": "op1"},
            gerar_comprovante("op1", tx, "validacao", v),
        )
        svc.registrar_resposta_cliente(tx, "Sim", False)
        svc.reservar_validacao(tx, "op2")
    finally:
        liberar.set()
        worker.join(3)
    caso = svc.obter_caso(tx)
    assert not worker.is_alive() and not erros
    assert caso.estado == EstadoCaso.PENDENTE_EXECUCAO
    assert caso.run_validacao["operacao_id"] == "op2"
    assert caso.run_validacao["run_id"] == ""
    assert len(caso.validacoes) == 1


@pytest.mark.parametrize("fase", ["analise", "validacao"])
@pytest.mark.parametrize(
    "defeito", ["failed", "cancelled", "error", "sem_json", "contrato", "evidencia", "outra_tx"]
)
def test_resultado_terminal_inutilizavel_encerra_sem_decisao(cenario, fase, defeito):
    tx, svc = cenario
    if fase == "analise":
        svc = ServicoCasos()
        svc.iniciar_analise(tx, "manual")
    svc.registrar_run_pendente(tx, {"run_id": "r", "operacao_id": "op", "agente": fase}, fase)
    payload = dados(fase, tx)
    if defeito == "contrato":
        payload["versao"] = 99
    if defeito == "evidencia":
        payload["evidencias"][0]["caminho"] = "/inventado"
    if defeito == "outra_tx":
        payload["transacao_id"] = "TX-INEXISTENTE"

    class IBM:
        def consultar_run(self, *args, **kwargs):
            return {
                "status": defeito if defeito in {"failed", "cancelled", "error"} else "completed",
                "output": "sem resultado" if defeito == "sem_json" else json.dumps(payload),
            }

        extrair_json_resposta = staticmethod(extrair_json_resposta)

    coord = Coordenador(svc, IBM())
    caso = coord.consultar_run_pendente(tx)
    assert caso.estado == EstadoCaso.PENDENTE
    assert caso.to_dict()["falha_integracao"]["fase"] == fase
    assert not caso.decisao_aplicada
    assert not caso.validacoes
    if fase == "analise":
        assert caso.analise is None and not caso.comprovantes


@pytest.mark.parametrize("defeito", ["ausente", "hash", "transacao", "chamada", "erro"])
def test_json_final_sem_comprovante_correspondente_nao_autoriza(cenario, defeito):
    tx, _ = cenario
    svc = ServicoCasos()
    svc.iniciar_analise(tx, "manual")
    svc.registrar_run_pendente(
        tx, {"run_id": "r", "operacao_id": "op", "agente": "analise"}, "analise"
    )
    resposta = AdaptadorFake.resultado(dados("analise", tx))
    passos = resposta["result"]["data"]["message"]["step_history"]
    if defeito == "ausente":
        passos.clear()
    elif defeito == "chamada":
        passos[-1]["step_details"][0]["tool_calls"][0]["id"] = "outra"
    else:
        retorno = passos[-1]["step_details"][1]
        comp = json.loads(retorno["content"])
        comp[{"hash": "hash_analise", "transacao": "transacao_id", "erro": "status"}[defeito]] = (
            "invalido"
        )
        retorno["content"] = json.dumps(comp)

    class IBM:
        def consultar_run(self, *args, **kwargs):
            return resposta

        extrair_json_resposta = staticmethod(extrair_json_resposta)

    caso = Coordenador(svc, IBM()).consultar_run_pendente(tx)
    assert caso.estado == EstadoCaso.PENDENTE
    assert caso.analise is None and not caso.comprovantes


def test_dois_leitores_aplicam_uma_pergunta_apenas(cenario):
    tx, svc = cenario
    barreira = threading.Barrier(2)

    class IBM:
        def consultar_run(self, *args, **kwargs):
            barreira.wait(3)
            return AdaptadorFake.resultado(dados("validacao", tx))

        extrair_json_resposta = staticmethod(extrair_json_resposta)

    coord = Coordenador(svc, IBM())
    erros = []

    def consultar():
        try:
            coord.consultar_run_pendente(tx)
        except Exception as exc:
            erros.append(exc)

    workers = [threading.Thread(target=consultar) for _ in range(2)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(3)
    assert not erros and not any(w.is_alive() for w in workers)
    caso = svc.obter_caso(tx)
    assert len(caso.validacoes) == 1 and len(caso.conversa) == 1
    snapshot = caso.to_dict(True)
    snapshot["conversa"][0]["texto"] = "Alterado fora do serviço"
    assert caso.conversa[0]["texto"] == "Reconhece a compra?"


def test_timeout_chega_ao_socket_real(monkeypatch):
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from ibm import orchestrate
    from web.servidor import _criar_adaptador_ibm
    from fraude.coordenador import _aguardar_run_com_timeout

    liberar = threading.Event()
    entrou = threading.Event()

    class Lento(BaseHTTPRequestHandler):
        def do_GET(self):
            entrou.set()
            liberar.wait(3)

        def log_message(self, *args):
            pass

    servidor = ThreadingHTTPServer(("127.0.0.1", 0), Lento)
    worker = threading.Thread(target=servidor.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setenv("WXO_SERVICE_URL", f"http://127.0.0.1:{servidor.server_port}")
    monkeypatch.setenv("WXO_API_KEY", "ficticia")
    monkeypatch.setattr(orchestrate, "token_atual", lambda: "ficticio")
    try:
        inicio = time.monotonic()
        with pytest.raises(TimeoutError):
            _aguardar_run_com_timeout(_criar_adaptador_ibm(), "run-lento", 0.15)
        assert entrou.is_set()
        assert time.monotonic() - inicio < 2
    finally:
        liberar.set()
        servidor.shutdown()
        servidor.server_close()
        worker.join(3)


def test_consultar_run_pendente_evita_ibm_quando_worker_ativo(cenario):
    """
    consultar_run_pendente deve retornar o estado local sem chamar IBM
    quando o worker de background consultou o mesmo run recentemente
    (dentro de _POLL_INTERVALO_INICIAL_S segundos).

    Isso evita que a rota POST /consultar do frontend dispare GETs IBM
    simultâneos com o worker enquanto ele ainda está em andamento.
    """
    import time
    from fraude.coordenador import _POLL_INTERVALO_INICIAL_S

    tx, svc = cenario
    chamadas_ibm = []

    class IBM:
        def consultar_run(self, run_id: str, timeout=None):
            chamadas_ibm.append(run_id)
            return {"status": "running"}

        extrair_json_resposta = staticmethod(extrair_json_resposta)

    coord = Coordenador(svc, IBM())

    # Simula worker: registra timestamp recente no mapa de timestamps
    coord._ultima_consulta_worker["v1"] = time.monotonic()

    # Consulta frontend — deve devolver estado local sem chamar IBM
    caso = coord.consultar_run_pendente(tx)

    assert chamadas_ibm == [], (
        "consultar_run_pendente chamou IBM mesmo com worker ativo recentemente. "
        "Deve retornar estado local para evitar consultas concorrentes."
    )
    assert caso.estado == EstadoCaso.PENDENTE_EXECUCAO


def test_consultar_run_pendente_chama_ibm_quando_worker_inativo(cenario):
    """
    consultar_run_pendente deve chamar IBM quando não há poll recente do worker,
    garantindo que o frontend possa detectar conclusões quando o worker parou.
    """
    import time
    from fraude.coordenador import _POLL_INTERVALO_INICIAL_S

    tx, svc = cenario
    chamadas_ibm = []

    class IBM:
        def consultar_run(self, run_id: str, timeout=None):
            chamadas_ibm.append(run_id)
            return {"status": "running"}

        extrair_json_resposta = staticmethod(extrair_json_resposta)

    coord = Coordenador(svc, IBM())

    # Worker inativo: timestamp muito antigo (bem além do intervalo)
    coord._ultima_consulta_worker["v1"] = time.monotonic() - _POLL_INTERVALO_INICIAL_S - 1.0

    coord.consultar_run_pendente(tx)

    assert chamadas_ibm == ["v1"], (
        "consultar_run_pendente deveria ter chamado IBM quando o worker está inativo."
    )
