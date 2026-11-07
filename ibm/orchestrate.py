"""
ibm/orchestrate.py — Adaptador REST para a Runs API do watsonx Orchestrate.

Gerencia thread_id e run_id associados à transação e ao agente corretos.
Não reutiliza resultados de execuções anteriores.
Utiliza context_variables na raiz da requisição conforme especificação.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any

from ibm.iam import token_atual

logger = logging.getLogger(__name__)

_POLL_INTERVALO = 3  # segundos entre consultas de status
_POLL_MAX_LOCAL = 10  # tentativas antes de retornar pendente_execucao


def _url_base() -> str:
    url = os.environ.get("WXO_SERVICE_URL", "").rstrip("/")
    if not url:
        raise RuntimeError("WXO_SERVICE_URL não configurada.")
    return url


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token_atual()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _post(endpoint: str, payload: dict) -> dict:
    url = f"{_url_base()}{endpoint}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=_headers(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Erro HTTP {exc.code} em POST {endpoint}.") from None


# Timeout padrão para requisições GET de status (usado quando não há orçamento restante)
_GET_TIMEOUT_PADRAO = 30


def _get(endpoint: str, timeout: float = _GET_TIMEOUT_PADRAO) -> dict:
    """
    Requisição GET ao endpoint especificado.

    O parâmetro timeout recebe o orçamento restante do polling quando disponível,
    evitando que uma única requisição HTTP consuma mais tempo que o orçamento local.
    """
    url = f"{_url_base()}{endpoint}"
    req = urllib.request.Request(url, headers=_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Erro HTTP {exc.code} em GET {endpoint}.") from None


# ---------------------------------------------------------------------------
# Criar run e aguardar (com limite local)
# ---------------------------------------------------------------------------


def criar_run(
    agente_id: str,
    mensagem: str,
    thread_id: str | None,
    context_variables: dict[str, Any],
) -> dict:
    """
    Cria uma run na Runs API.
    Envia context_variables na raiz da requisição conforme documentação.
    Retorna {thread_id, run_id}.
    """
    payload: dict[str, Any] = {
        "message": {
            "role": "user",
            "content": mensagem,
        },
        "agent_id": agente_id,
        "context_variables": context_variables,
    }
    if thread_id:
        payload["thread_id"] = thread_id

    resposta = _post("/v1/orchestrate/runs", payload)
    if not resposta.get("run_id") or not resposta.get("thread_id"):
        raise RuntimeError("A criação da execução não retornou run_id e thread_id.")
    return {
        "thread_id": resposta.get("thread_id", ""),
        "run_id": resposta.get("run_id", ""),
    }


def consultar_run(run_id: str, timeout: float = _GET_TIMEOUT_PADRAO) -> dict:
    """
    Consulta o status de uma run existente.

    O parâmetro timeout permite que o coordenador passe o orçamento restante
    do polling local, limitando o tempo que esta requisição pode consumir.
    Usa _GET_TIMEOUT_PADRAO (30s) quando não há restrição de orçamento.
    """
    return _get(f"/v1/orchestrate/runs/{run_id}", timeout=timeout)


def aguardar_run(run_id: str, max_tentativas: int = _POLL_MAX_LOCAL) -> dict:
    """
    Aguarda a conclusão de uma run com limite de tentativas local.

    Se o run ainda estiver ativo ao esgotar o limite, retorna o status atual
    sem cancelar a operação. O run_id é preservado para consulta posterior.

    Nunca cancela uma operação legítima só porque o timeout local expirou.
    """
    for tentativa in range(max_tentativas):
        status = consultar_run(run_id)
        estado = (status.get("status") or "").lower()
        if estado in {"completed", "failed", "cancelled", "error"}:
            return status
        if tentativa < max_tentativas - 1:
            time.sleep(_POLL_INTERVALO)
    # Retorna o último status sem forçar conclusão
    return status  # type: ignore[return-value]


def extrair_resposta_texto(run_status: dict) -> str | None:
    """
    Extrai o texto da última mensagem do assistente.

    Formato da Runs API:
      run_status.result.data.message.content[].text  (response_type == "text")
    """
    # Caminho principal: result.data.message.content[].text
    resultado = run_status.get("result")
    if isinstance(resultado, dict):
        msg = resultado.get("data", {}).get("message", {})
        conteudo = msg.get("content", [])
        if isinstance(conteudo, list):
            for bloco in conteudo:
                if isinstance(bloco, dict) and bloco.get("response_type") == "text":
                    texto = bloco.get("text", "")
                    if texto and texto.strip():
                        return texto.strip()
        # Fallback: role=assistant em content
        if isinstance(conteudo, str) and conteudo.strip():
            return conteudo.strip()

    # Fallback legado: output (str) ou messages[]
    for campo in ("output",):
        valor = run_status.get(campo)
        if isinstance(valor, str) and valor.strip():
            return valor.strip()

    mensagens = run_status.get("messages", [])
    for msg in reversed(mensagens):
        if msg.get("role") in ("assistant", "agent"):
            conteudo = msg.get("content", "")
            if isinstance(conteudo, str) and conteudo.strip():
                return conteudo.strip()
            if isinstance(conteudo, list):
                for bloco in conteudo:
                    if isinstance(bloco, dict) and bloco.get("type") == "text":
                        texto = bloco.get("text", "")
                        if texto.strip():
                            return texto.strip()

    return None


def extrair_json_resposta(run_status: dict) -> dict | None:
    """
    Tenta extrair o JSON estruturado da resposta do agente.

    Usa extração baseada em pilha para suportar aninhamento arbitrário
    (ex.: evidencias com objetos internos).  Prefere o último bloco JSON
    de maior profundidade encontrado no texto — que normalmente é o
    contrato completo da análise ou validação.
    """
    texto = extrair_resposta_texto(run_status)
    if not texto:
        return None

    logger.debug("extrair_json_resposta: texto bruto (%d chars): %.500s", len(texto), texto)

    # Tenta parse direto (resposta já é JSON puro)
    texto_limpo = texto.strip()
    if texto_limpo.startswith("{"):
        try:
            return json.loads(texto_limpo)
        except json.JSONDecodeError:
            pass

    # Extração baseada em pilha — suporta aninhamento arbitrário
    candidatos: list[dict] = []
    pilha: list[int] = []
    for i, ch in enumerate(texto):
        if ch == "{":
            pilha.append(i)
        elif ch == "}" and pilha:
            inicio = pilha.pop()
            if not pilha:  # fechou um objeto de nível zero
                fragmento = texto[inicio : i + 1]
                try:
                    obj = json.loads(fragmento)
                    if isinstance(obj, dict):
                        candidatos.append(obj)
                except json.JSONDecodeError:
                    pass

    if not candidatos:
        return None

    # Prefere o candidato com mais chaves (mais completo)
    candidatos.sort(key=lambda d: len(d), reverse=True)
    melhor = candidatos[0]
    logger.debug("extrair_json_resposta: candidato escolhido com %d chaves", len(melhor))
    return melhor


# ---------------------------------------------------------------------------
# Fake para testes (não usa IBM)
# ---------------------------------------------------------------------------


class AdaptadorFake:
    """
    Adaptador substituto para testes — nunca acessa a IBM.
    Deve ser usado APENAS em testes ou em execução de teste explicitamente identificada.
    """

    def __init__(
        self, resposta_analise: dict | None = None, resposta_validacao: dict | None = None
    ) -> None:
        self._resposta_analise = resposta_analise
        self._resposta_validacao = resposta_validacao
        self.chamadas: list[dict] = []

    def criar_run(
        self,
        agente_id: str,
        mensagem: str,
        thread_id: str | None,
        context_variables: dict,
    ) -> dict:
        self.chamadas.append(
            {
                "agente_id": agente_id,
                "mensagem": mensagem,
                "context_variables": context_variables,
            }
        )
        return {"thread_id": "fake-thread-001", "run_id": "fake-run-001"}

    def aguardar_run(self, run_id: str, max_tentativas: int = 1) -> dict:
        if "analise" in run_id or self._resposta_analise:
            return self.resultado(self._resposta_analise or {})
        return self.resultado(self._resposta_validacao or {})

    @staticmethod
    def resultado(payload: dict) -> dict:
        """Resposta externa fictícia, incluindo passos das ferramentas, para testes."""
        from fraude.contratos import calcular_hash

        tx = payload.get("transacao_id", "")
        passos = []

        def ferramenta(nome, retorno):
            ident = "fake-" + nome
            passos.append(
                {
                    "step_details": [
                        {
                            "type": "tool_calls",
                            "tool_calls": [
                                {"id": ident, "name": nome, "args": {"transacao_id": tx}}
                            ],
                        },
                        {
                            "type": "tool_response",
                            "tool_call_id": ident,
                            "name": nome,
                            "content": json.dumps(retorno),
                        },
                    ]
                }
            )

        if "risco" in payload:
            ferramenta("consultar_contexto", {"status": "ok", "transacao": {"id": tx}})
            ferramenta(
                "registrar_analise",
                {
                    "status": "ok",
                    "fase": "analise",
                    "transacao_id": tx,
                    "risco": payload["risco"],
                    "acao": payload.get("acao"),
                    "hash_analise": calcular_hash(payload),
                },
            )
        elif payload.get("desfecho") != "aguardar_cliente":
            ferramenta(
                "executar_decisao",
                {
                    "status": "ok",
                    "fase": "validacao",
                    "transacao_id": tx,
                    "desfecho": payload.get("desfecho"),
                    "simulado": True,
                    "hash_validacao": calcular_hash(payload),
                },
            )
        return {
            "status": "completed",
            "output": json.dumps(payload),
            "result": {"data": {"message": {"step_history": passos}}},
        }

    def consultar_run(self, run_id: str, timeout: float | None = None) -> dict:
        return self.aguardar_run(run_id)

    def extrair_json_resposta(self, run_status: dict) -> dict | None:
        saida = run_status.get("output", "")
        if saida:
            try:
                return json.loads(saida)
            except Exception:
                return None
        return None
