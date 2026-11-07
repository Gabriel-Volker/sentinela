"""
web/servidor.py — Servidor HTTP do Sentinela.

Usa apenas a biblioteca padrão do Python.
Serve a API JSON e os arquivos estáticos permitidos.
Restringe host ao ambiente local.
Bloqueia travessia de diretórios e acesso a arquivos sensíveis.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

# Garante que o diretório raiz do projeto está no path
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.environ.setdefault("SENTINELA_ROOT", str(_ROOT))

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env", override=False)

from fraude.coordenador import Coordenador
from fraude.servico import ServicoCasos
from web.rotas import Roteador

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"

# Arquivos estáticos permitidos (lista explícita — sem acesso arbitrário)
_ARQUIVOS_PERMITIDOS = {
    "/": "index.html",
    "/index.html": "index.html",
    "/static/css/sentinela.css": "css/sentinela.css",
    "/static/js/app.js": "js/app.js",
    "/static/js/ui.js": "js/ui.js",
    "/static/js/api.js": "js/api.js",
    "/static/img/persona-ana.svg": "img/persona-ana.svg",
    "/static/img/persona-lucas.svg": "img/persona-lucas.svg",
    "/static/img/persona-roberto.svg": "img/persona-roberto.svg",
    "/favicon.ico": "favicon.ico",
}

# Origens permitidas (somente local)
_ORIGENS_PERMITIDAS = {
    "http://127.0.0.1",
    "http://localhost",
    "null",  # file:// em alguns browsers
}


def _origem_permitida(origin: str, porta: int) -> bool:
    if not origin:
        return True  # sem Origin header — aceita (curl, fetch local)
    base = origin.rstrip("/")
    if base in _ORIGENS_PERMITIDAS:
        return True
    if base in {f"http://127.0.0.1:{porta}", f"http://localhost:{porta}"}:
        return True
    return False


def _criar_adaptador_ibm():
    """Cria o adaptador IBM se as credenciais estiverem configuradas."""
    if os.environ.get("WXO_SERVICE_URL") and os.environ.get("WXO_API_KEY"):
        import ibm.orchestrate as orch

        class AdaptadorReal:
            def criar_run(self, agente_id, mensagem, thread_id, context_variables):
                return orch.criar_run(agente_id, mensagem, thread_id, context_variables)

            def aguardar_run(self, run_id, max_tentativas=15):
                return orch.aguardar_run(run_id, max_tentativas=max_tentativas)

            def consultar_run(self, run_id, timeout=None):
                # Repassa o orçamento restante ao módulo de transporte.
                # Sem isso o Coordenador captura TypeError e consulta sem timeout.
                if timeout is not None:
                    return orch.consultar_run(run_id, timeout=timeout)
                return orch.consultar_run(run_id)

            def extrair_json_resposta(self, run_status):
                return orch.extrair_json_resposta(run_status)

        return AdaptadorReal()

    logger.warning(
        "WXO_SERVICE_URL ou WXO_API_KEY não configurados. "
        "Servidor iniciará em modo offline (sem integração IBM)."
    )
    return None


# ---------------------------------------------------------------------------
# Handler HTTP
# ---------------------------------------------------------------------------


class SentinelaHandler(BaseHTTPRequestHandler):
    roteador: Roteador
    porta: int

    def log_message(self, fmt, *args):
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _enviar(self, codigo: int, dados: Any) -> None:
        body = json.dumps(dados, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _cors_headers(self) -> None:
        origin = self.headers.get("Origin", "")
        if _origem_permitida(origin, self.porta):
            self.send_header("Access-Control-Allow-Origin", origin or "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        origin = self.headers.get("Origin", "")
        if not _origem_permitida(origin, self.porta):
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        self._processar("GET")

    def do_POST(self):
        self._processar("POST")

    def _processar(self, metodo: str) -> None:
        origem = self.headers.get("Origin", "")
        if origem and not _origem_permitida(origem, self.porta):
            self._enviar(403, {"status": "erro", "mensagem": "Origem não permitida."})
            return

        # Decompõe path e query
        path = self.path
        query = ""
        if "?" in path:
            path, _, query = path.partition("?")

        # Arquivos estáticos
        if metodo == "GET":
            arquivo = _ARQUIVOS_PERMITIDOS.get(path)
            if arquivo:
                self._servir_estatico(arquivo)
                return

        # API JSON
        if (
            not path.startswith("/health")
            and not path.startswith("/catalogo")
            and not path.startswith("/contexto")
            and not path.startswith("/casos")
            and not path.startswith("/exportar")
            and not path.startswith("/sessao")
        ):
            self._enviar(404, {"status": "erro", "mensagem": "Recurso não encontrado."})
            return

        content_length = int(self.headers.get("Content-Length", 0) or 0)
        codigo, dados = self.roteador.despachar(
            metodo, path, query, self.rfile, content_length, dict(self.headers)
        )
        self._enviar(codigo, dados)

    def _servir_estatico(self, relativo: str) -> None:
        caminho = _STATIC_DIR / relativo
        if not caminho.exists():
            self._enviar(404, {"status": "erro", "mensagem": "Arquivo não encontrado."})
            return
        try:
            conteudo = caminho.read_bytes()
        except OSError:
            self._enviar(500, {"status": "erro", "mensagem": "Erro ao ler arquivo."})
            return
        mime = mimetypes.guess_type(str(caminho))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(conteudo)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(conteudo)


# ---------------------------------------------------------------------------
# Inicialização
# ---------------------------------------------------------------------------


def criar_app(
    ibm_adapter=None,
    agente_analise_id: str | None = None,
    agente_validacao_id: str | None = None,
) -> tuple[ServicoCasos, Coordenador, Roteador]:
    """Cria as dependências principais. Permite injeção em testes.

    Sem IDs explícitos, o coordenador lê os agentes das variáveis de ambiente.
    """
    servico = ServicoCasos()
    adaptador = ibm_adapter or _criar_adaptador_ibm()
    coordenador = Coordenador(
        servico,
        adaptador,
        agente_analise_id=agente_analise_id,
        agente_validacao_id=agente_validacao_id,
    )
    roteador = Roteador(servico, coordenador)
    return servico, coordenador, roteador


def iniciar(host: str = "127.0.0.1", porta: int = 8080, ibm_adapter=None) -> None:
    """Inicia o servidor e bloqueia até sinal de encerramento."""
    servico, coordenador, roteador = criar_app(ibm_adapter)

    # Injeta roteador na classe do handler via closure
    class Handler(SentinelaHandler):
        pass

    Handler.roteador = roteador
    Handler.porta = porta

    servidor = HTTPServer((host, porta), Handler)

    def _encerrar(signum, frame):
        logger.info("Encerrando servidor Sentinela...")
        t = threading.Thread(target=servidor.shutdown)
        t.start()

    signal.signal(signal.SIGINT, _encerrar)
    signal.signal(signal.SIGTERM, _encerrar)

    print(f"\n{'=' * 60}")
    print("  Sentinela — MVP de análise de fraudes")
    print(f"  URL: http://{host}:{porta}")
    print("  Ambiente: demonstração local com dados sintéticos")
    print("  Operações bancárias: SIMULADAS")
    ibm_ok = bool(os.environ.get("WXO_SERVICE_URL") and os.environ.get("WXO_API_KEY"))
    print(f"  Integração IBM: {'CONFIGURADA' if ibm_ok else 'NÃO CONFIGURADA (modo offline)'}")
    print(f"{'=' * 60}\n")

    logger.info("Servidor iniciado em http://%s:%d", host, porta)
    try:
        servidor.serve_forever()
    finally:
        servidor.server_close()
        logger.info("Servidor encerrado.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    porta = int(os.environ.get("SENTINELA_PORT", "8080"))
    host = os.environ.get("SENTINELA_HOST", "127.0.0.1")
    iniciar(host=host, porta=porta)
