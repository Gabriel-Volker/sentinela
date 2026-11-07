"""Executa análise, pergunta e resposta pelas rotas HTTP e pela IBM reais.

Use somente transações sintéticas de desenvolvimento. Não roda na suíte rápida.
Não cancela a execução remota se o limite local de acompanhamento terminar.
"""

from __future__ import annotations

import argparse
import http.client
import json
import sys
import threading
import time
from http.server import HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from web.servidor import SentinelaHandler, criar_app


def verificar(transacao_id, resposta_cliente, prazo_s=180):
    servico, coordenador, roteador = criar_app()

    class Handler(SentinelaHandler):
        pass

    Handler.roteador = roteador
    servidor = HTTPServer(("127.0.0.1", 0), Handler)
    Handler.porta = servidor.server_port
    worker = threading.Thread(target=servidor.serve_forever, daemon=True)
    worker.start()
    etapas = []

    def requisitar(acao, corpo=None):
        inicio = time.monotonic()
        conn = http.client.HTTPConnection("127.0.0.1", servidor.server_port, timeout=120)
        try:
            conn.request(
                "POST",
                f"/casos/{transacao_id}/{acao}",
                json.dumps(corpo or {}),
                {"Content-Type": "application/json"},
            )
            resp = conn.getresponse()
            dados = json.loads(resp.read())
            if resp.status != 200:
                raise RuntimeError(f"Rota {acao} retornou HTTP {resp.status}.")
            caso = dados["dados"]
        finally:
            conn.close()
        etapas.append(
            {
                "rota": acao,
                "estado": caso["estado"],
                "duracao_s": round(time.monotonic() - inicio, 3),
            }
        )
        print(f"{acao}: {caso['estado']}", flush=True)
        return caso

    def acompanhar(caso):
        limite = time.monotonic() + prazo_s
        while caso["estado"] in {"pendente_execucao", "validando", "analisando"}:
            if time.monotonic() >= limite:
                raise TimeoutError("Acompanhamento local encerrado; execução remota preservada.")
            time.sleep(2)
            caso = requisitar("consultar")
        return caso

    try:
        caso = acompanhar(requisitar("analisar", {"modo": "manual"}))
        if caso.get("falha_integracao"):
            raise RuntimeError(
                "Análise sem resultado verificável: " + caso["falha_integracao"]["codigo"]
            )
        if caso["estado"] != "aguardando_cliente":
            raise RuntimeError(
                "O agente não classificou risco médio; fluxo de conversa não exercitado."
            )
        if not any(t["papel"] == "agente" for t in caso.get("conversa", [])):
            raise RuntimeError("Pergunta não apareceu na resposta HTTP.")
        caso = acompanhar(
            requisitar("responder", {"texto": resposta_cliente, "sem_resposta": False})
        )
        if caso.get("falha_integracao"):
            raise RuntimeError(
                "Validação sem resultado verificável: " + caso["falha_integracao"]["codigo"]
            )
        if caso["estado"] not in {"autorizado", "bloqueado", "pendente"}:
            raise RuntimeError("Validação não alcançou um desfecho final.")
        comprovantes = caso.get("comprovantes", [])
        if len(comprovantes) < 3:
            raise RuntimeError("Fluxo não registrou análise, pergunta e decisão verificadas.")
        return {
            "transacao_id": transacao_id,
            "estado_final": caso["estado"],
            "risco": caso["analise"]["risco"],
            "etapas": etapas,
            "comprovantes_verificados": len(comprovantes),
            "operacoes_bancarias": "simuladas",
        }
    finally:
        servidor.shutdown()
        servidor.server_close()
        worker.join(3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transacao", required=True)
    parser.add_argument("--resposta", required=True)
    args = parser.parse_args()
    print(json.dumps(verificar(args.transacao, args.resposta), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
