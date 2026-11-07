"""
tests/test_http.py — Testes das rotas HTTP.

Sem IBM, sem credenciais. Testa entrada, conflito, recurso ausente e isolamento.
"""

from ibm.orchestrate import AdaptadorFake as IBMFake

import json
import threading
import time
from http.client import HTTPConnection

import pytest

from fraude.repositorio import listar_candidatas_cliente, listar_clientes
from ibm.orchestrate import AdaptadorFake
from web.servidor import SentinelaHandler, criar_app

# ---------------------------------------------------------------------------
# Fixture: servidor local em thread para testes de integração HTTP
# ---------------------------------------------------------------------------


def _tx_id_real():
    clientes = listar_clientes()
    for c in clientes:
        cands = listar_candidatas_cliente(c["id"])
        if cands:
            return cands[0]["id"]
    pytest.skip("Nenhuma candidata disponível")


class ServidorTeste:
    """Sobe o servidor em thread e fecha ao sair do contexto."""

    def __init__(self, porta: int = 18080):
        self.porta = porta
        self._thread = None
        self._srv = None

    def __enter__(self):
        from http.server import HTTPServer

        tx_id = _tx_id_real()
        analise_fake = {
            "versao": 2,
            "transacao_id": tx_id,
            "risco": "baixo",
            "acao": "autorizar",
            "evidencias": [{"caminho": "/valor", "descricao": "Valor."}],
            "informacoes_ausentes": [],
            "justificativa": "Teste.",
        }
        fake = AdaptadorFake(resposta_analise=analise_fake)
        svc, coord, roteador = criar_app(
            ibm_adapter=fake, agente_analise_id="fake-analise", agente_validacao_id="fake-validacao"
        )

        class Handler(SentinelaHandler):
            pass

        Handler.roteador = roteador
        Handler.porta = self.porta

        self._srv = HTTPServer(("127.0.0.1", self.porta), Handler)
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()
        time.sleep(0.2)
        return self

    def __exit__(self, *args):
        if self._srv:
            self._srv.shutdown()

    def req(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        conn = HTTPConnection("127.0.0.1", self.porta, timeout=10)
        encoded = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if encoded:
            headers["Content-Length"] = str(len(encoded))
        conn.request(method, path, body=encoded, headers=headers)
        resp = conn.getresponse()
        data = json.loads(resp.read())
        conn.close()
        return resp.status, data


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


def test_health():
    with ServidorTeste(18080) as srv:
        status, data = srv.req("GET", "/health")
        assert status == 200
        assert data["status"] == "ok"


# ---------------------------------------------------------------------------
# GET /catalogo
# ---------------------------------------------------------------------------


def test_catalogo_retorna_clientes():
    with ServidorTeste(18081) as srv:
        status, data = srv.req("GET", "/catalogo")
        assert status == 200
        assert "clientes" in data["dados"]
        assert len(data["dados"]["clientes"]) > 0


def test_catalogo_busca_texto():
    with ServidorTeste(18082) as srv:
        status, data = srv.req("GET", "/catalogo?q=silva")
        assert status == 200
        # Pode não ter resultados, mas deve responder
        assert "clientes" in data["dados"]


def test_catalogo_resultado_vazio():
    with ServidorTeste(18083) as srv:
        status, data = srv.req("GET", "/catalogo?q=xxxxxxxxzzzzzqqqqq")
        assert status == 200
        assert data["dados"]["clientes"] == []
        assert data["dados"]["total"] == 0


# ---------------------------------------------------------------------------
# GET /contexto/{id}
# ---------------------------------------------------------------------------


def test_contexto_tx_existente():
    tx_id = _tx_id_real()
    with ServidorTeste(18084) as srv:
        status, data = srv.req("GET", f"/contexto/{tx_id}")
        assert status == 200
        assert "transacao" in data["dados"]


def test_contexto_sem_roteiro_cliente():
    """O roteiro do cliente não pode ser exposto pelo frontend."""
    tx_id = _tx_id_real()
    with ServidorTeste(18085) as srv:
        _, data = srv.req("GET", f"/contexto/{tx_id}")
        tx = data["dados"].get("transacao", {})
        assert "roteiro_cliente" not in tx


def test_contexto_tx_inexistente():
    with ServidorTeste(18086) as srv:
        status, data = srv.req("GET", "/contexto/TX-INEXISTENTE-99")
        assert status == 404


def test_contexto_id_invalido():
    with ServidorTeste(18087) as srv:
        status, data = srv.req("GET", "/contexto/../etc/passwd")
        # Travessia de diretório bloqueada
        assert status in (400, 404)


# ---------------------------------------------------------------------------
# GET /casos/{id}
# ---------------------------------------------------------------------------


def test_casos_caso_novo_retorna_estado_novo():
    tx_id = _tx_id_real()
    with ServidorTeste(18088) as srv:
        status, data = srv.req("GET", f"/casos/{tx_id}")
        assert status == 200
        assert data["dados"]["estado"] == "novo"


# ---------------------------------------------------------------------------
# POST /casos/{id}/analisar
# ---------------------------------------------------------------------------


def test_analisar_modo_invalido():
    tx_id = _tx_id_real()
    with ServidorTeste(18089) as srv:
        status, data = srv.req("POST", f"/casos/{tx_id}/analisar", {"modo": "invalido"})
        assert status == 400


def test_analisar_sem_modo():
    tx_id = _tx_id_real()
    with ServidorTeste(18090) as srv:
        status, data = srv.req("POST", f"/casos/{tx_id}/analisar", {})
        assert status == 400


def test_analisar_tx_inexistente():
    with ServidorTeste(18091) as srv:
        status, data = srv.req("POST", "/casos/TX-FAKE999/analisar", {"modo": "manual"})
        assert status in (404, 503)  # 404 tx não encontrada, 503 IBM offline


# ---------------------------------------------------------------------------
# POST /casos/{id}/responder — validações
# ---------------------------------------------------------------------------


def test_responder_texto_vazio():
    tx_id = _tx_id_real()
    with ServidorTeste(18092) as srv:
        # Precisaria estar em aguardando_cliente — só testa validação de entrada
        status, data = srv.req(
            "POST", f"/casos/{tx_id}/responder", {"texto": "", "sem_resposta": False}
        )
        # Estado incorreto OU texto vazio → deve dar erro
        assert status in (400, 409)


def test_responder_texto_com_sem_resposta():
    tx_id = _tx_id_real()
    with ServidorTeste(18093) as srv:
        status, data = srv.req(
            "POST", f"/casos/{tx_id}/responder", {"texto": "sim", "sem_resposta": True}
        )
        assert status == 400


# ---------------------------------------------------------------------------
# Rota inexistente
# ---------------------------------------------------------------------------


def test_rota_inexistente():
    with ServidorTeste(18094) as srv:
        status, data = srv.req("GET", "/rota/inexistente/xyz")
        assert status == 404


# ---------------------------------------------------------------------------
# Arquivo estático servido
# ---------------------------------------------------------------------------


def test_index_html_servido():
    with ServidorTeste(18095):
        conn = HTTPConnection("127.0.0.1", 18095, timeout=10)
        conn.request("GET", "/")
        resp = conn.getresponse()
        content_type = resp.getheader("Content-Type", "")
        body = resp.read()
        conn.close()
        assert resp.status == 200
        assert "text/html" in content_type
        assert b"Sentinela" in body


def test_arquivo_arbitrario_bloqueado():
    with ServidorTeste(18096):
        conn = HTTPConnection("127.0.0.1", 18096, timeout=10)
        conn.request("GET", "/dados/mock/avaliacao/gabarito_dev.json")
        resp = conn.getresponse()
        resp.read()
        conn.close()
        assert resp.status == 404


def test_env_nao_servido():
    with ServidorTeste(18097):
        conn = HTTPConnection("127.0.0.1", 18097, timeout=10)
        conn.request("GET", "/.env")
        resp = conn.getresponse()
        resp.read()
        conn.close()
        assert resp.status == 404


# ---------------------------------------------------------------------------
# GET /exportar
# ---------------------------------------------------------------------------


def test_exportar_sessao_vazia():
    with ServidorTeste(18098) as srv:
        status, data = srv.req("GET", "/exportar")
        assert status == 200
        exportacao = data["dados"]
        assert "casos" in exportacao
        assert "aviso" in exportacao
        # Não deve conter gabarito
        texto = json.dumps(exportacao)
        assert "rotulo" not in texto
        assert "cenario" not in texto


# ---------------------------------------------------------------------------
# GET /casos — listar sessão
# ---------------------------------------------------------------------------


def test_listar_casos_sessao_vazia():
    with ServidorTeste(18100) as srv:
        status, data = srv.req("GET", "/casos")
        assert status == 200
        assert isinstance(data["dados"], list)
        assert len(data["dados"]) == 0


# ---------------------------------------------------------------------------
# Fluxo completo: analisar → GET /casos lista o caso
# ---------------------------------------------------------------------------


def test_fluxo_analisar_baixo_risco_lista_sessao():
    """Análise de risco baixo deve autorizar e aparecer na listagem de sessão."""
    tx_id = _tx_id_real()
    analise_fake = {
        "versao": 2,
        "transacao_id": tx_id,
        "risco": "baixo",
        "acao": "autorizar",
        "evidencias": [{"caminho": "/valor", "descricao": "Valor dentro do padrão."}],
        "informacoes_ausentes": [],
        "justificativa": "Perfil consistente.",
    }
    import threading
    import time
    from http.server import HTTPServer

    fake = AdaptadorFake(resposta_analise=analise_fake)
    svc, coord, roteador = criar_app(
        ibm_adapter=fake, agente_analise_id="fake-analise", agente_validacao_id="fake-validacao"
    )

    class Handler(SentinelaHandler):
        pass

    Handler.roteador = roteador
    Handler.porta = 18101
    servidor = HTTPServer(("127.0.0.1", 18101), Handler)
    t = threading.Thread(target=servidor.serve_forever, daemon=True)
    t.start()
    time.sleep(0.2)

    try:
        conn = __import__("http.client", fromlist=["HTTPConnection"]).HTTPConnection(
            "127.0.0.1", 18101, timeout=10
        )

        def req(method, path, body=None):
            encoded = json.dumps(body).encode() if body is not None else None
            headers = {"Content-Type": "application/json"}
            if encoded:
                headers["Content-Length"] = str(len(encoded))
            conn.request(method, path, body=encoded, headers=headers)
            resp = conn.getresponse()
            data = json.loads(resp.read())
            return resp.status, data

        # Analisa
        status, data = req("POST", f"/casos/{tx_id}/analisar", {"modo": "manual"})
        assert status == 200, f"Análise falhou: {data}"
        assert data["dados"]["estado"] == "autorizado"
        assert data["dados"]["analise"]["risco"] == "baixo"

        # Lista casos — deve aparecer
        status, data = req("GET", "/casos")
        assert status == 200
        casos = data["dados"]
        assert len(casos) == 1
        assert casos[0]["transacao_id"] == tx_id
        assert casos[0]["estado"] == "autorizado"

        # Exportar — caso deve constar
        status, data = req("GET", "/exportar")
        assert status == 200
        assert len(data["dados"]["casos"]) == 1

        conn.close()
    finally:
        servidor.shutdown()


def test_fluxo_analisar_medio_risco_conversa():
    """Análise de risco médio → aguarda cliente → resposta → valida."""
    tx_id = _tx_id_real()
    analise_fake = {
        "versao": 2,
        "transacao_id": tx_id,
        "risco": "medio",
        "acao": "validar",
        "evidencias": [{"caminho": "/valor", "descricao": "Valor acima do ticket médio."}],
        "informacoes_ausentes": [],
        "justificativa": "Valor acima do padrão.",
    }
    validacao_pergunta = {
        "versao": 2,
        "transacao_id": tx_id,
        "desfecho": "aguardar_cliente",
        "pergunta": "Você reconhece esta compra?",
        "evidencias": [{"caminho": "/valor", "descricao": "Valor da transação."}],
        "justificativa": "Aguardando confirmação do cliente.",
    }
    validacao_autorizar = {
        "versao": 2,
        "transacao_id": tx_id,
        "desfecho": "autorizar",
        "pergunta": None,
        "evidencias": [{"caminho": "/valor", "descricao": "Cliente confirmou."}],
        "justificativa": "Cliente confirmou a compra.",
    }

    import threading
    import time
    from http.server import HTTPServer

    # Fake que retorna pergunta na 1ª chamada de validação, autoriza na 2ª
    chamadas = {"n": 0}

    class FakeSequencial(AdaptadorFake):
        def aguardar_run(self, run_id, max_tentativas=1):
            chamadas["n"] += 1
            # 1ª chamada = análise; 2ª = validação (pergunta); 3ª = validação (autorizar)
            if chamadas["n"] == 1:
                return {**IBMFake.resultado(analise_fake), "status": "completed"}
            elif chamadas["n"] == 2:
                return {**IBMFake.resultado(validacao_pergunta), "status": "completed"}
            else:
                return {**IBMFake.resultado(validacao_autorizar), "status": "completed"}

    fake = FakeSequencial()
    svc, coord, roteador = criar_app(
        ibm_adapter=fake, agente_analise_id="fake-analise", agente_validacao_id="fake-validacao"
    )

    class Handler(SentinelaHandler):
        pass

    Handler.roteador = roteador
    Handler.porta = 18102
    servidor = HTTPServer(("127.0.0.1", 18102), Handler)
    t = threading.Thread(target=servidor.serve_forever, daemon=True)
    t.start()
    time.sleep(0.2)

    try:
        from http.client import HTTPConnection

        conn = HTTPConnection("127.0.0.1", 18102, timeout=10)

        def req(method, path, body=None):
            encoded = json.dumps(body).encode() if body is not None else None
            headers = {"Content-Type": "application/json"}
            if encoded:
                headers["Content-Length"] = str(len(encoded))
            conn.request(method, path, body=encoded, headers=headers)
            resp = conn.getresponse()
            data = json.loads(resp.read())
            return resp.status, data

        # Analisa — risco médio → inicia validação automaticamente → aguarda cliente
        status, data = req("POST", f"/casos/{tx_id}/analisar", {"modo": "manual"})
        assert status == 200, f"Análise falhou: {data}"
        # A pergunta é assíncrona: consulta o mesmo run até ficar disponível.
        import time

        prazo = time.monotonic() + 3
        while data["dados"]["estado"] == "pendente_execucao" and time.monotonic() < prazo:
            status, data = req("POST", f"/casos/{tx_id}/consultar", {})
            time.sleep(0.01)
        assert data["dados"]["estado"] == "aguardando_cliente", (
            f"Estado esperado aguardando_cliente, recebido: {data['dados']['estado']}"
        )

        # Verifica que há uma pergunta na conversa
        conversa = data["dados"].get("conversa", [])
        perguntas = [t for t in conversa if t.get("papel") == "agente"]
        assert len(perguntas) >= 1

        # Responde — deve acionar validação final → autorizar
        status, data = req(
            "POST", f"/casos/{tx_id}/responder", {"texto": "Sim, fui eu.", "sem_resposta": False}
        )
        assert status == 200, f"Resposta falhou: {data}"
        assert data["dados"]["estado"] == "autorizado"

        conn.close()
    finally:
        servidor.shutdown()


def test_candidatas_cliente_endpoint():
    """GET /contexto/_candidatas/{id} retorna compras sem roteiro_cliente."""
    with ServidorTeste(18103) as srv:
        from fraude.repositorio import listar_clientes

        clientes = listar_clientes()
        assert clientes, "Nenhum cliente disponível"
        cliente_id = clientes[0].get("customer_id") or clientes[0].get("id")
        status, data = srv.req("GET", f"/contexto/_candidatas/{cliente_id}")
        assert status == 200
        candidatas = data["dados"]["candidatas"]
        assert isinstance(candidatas, list)
        for c in candidatas:
            assert "roteiro_cliente" not in c
