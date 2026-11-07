"""
ferramentas/contexto.py — Ferramenta remota: consultar_contexto.

Consulta o contexto operacional da transação a partir do ID fornecido pelo agente.
O agente recebe o ID da transação no prompt/contexto e passa como parâmetro.

Projeção de contexto:
  A transação bruta contém campos duplicados (amount/valor, currency_code/moeda, etc.)
  originados da integração com o schema Sicredi. A função _projetar_transacao() mantém
  os campos semânticamente relevantes para análise de risco e remove os aliases
  redundantes, reduzindo o payload sem perder sinais úteis.

  Campos preservados: todos os sinais de valor, localização, dispositivo, canal,
  estabelecimento e frequência necessários para a análise e para caminhos de evidência.
  Campos removidos: apenas aliases exatos que duplicam outro campo presente.
"""

from __future__ import annotations

from ibm_watsonx_orchestrate.agent_builder.tools.python_tool import tool


@tool(
    name="consultar_contexto",
    description=(
        "Consulta o contexto operacional da transacao em analise. "
        "Retorna dados da transacao, cliente, cartao, historico resumido e eventos recentes. "
        "Use esta ferramenta antes de qualquer avaliacao de risco. "
        "O transacao_id esta disponivel no contexto da execucao."
    ),
)
def consultar_contexto(transacao_id: str) -> dict:
    """
    Obtém o contexto operacional de uma transação.

    Args:
        transacao_id: Identificador da transação a ser analisada (ex: TX-XXXXXXXX).

    Returns:
        Contexto operacional com transacao, cliente, cartao, historico e eventos.
        A transacao retornada é uma projeção sem campos duplicados. Os caminhos
        de evidência devem apontar para campos dessa projeção (ex: /valor, /canal).
    """
    import json
    import os
    from pathlib import Path

    if not transacao_id or not transacao_id.strip():
        return {"status": "erro", "mensagem": "transacao_id e obrigatorio."}

    transacao_id = transacao_id.strip()

    # Localiza os dados — tenta múltiplos caminhos base
    def _encontrar_dados(nome: str):
        cwd = Path(os.getcwd())
        script_dir = Path(__file__).parent
        candidatos = [
            cwd / "dados" / "mock" / "operacional" / nome,
            script_dir / "dados" / "mock" / "operacional" / nome,
            script_dir.parent / "dados" / "mock" / "operacional" / nome,
            Path("/app") / "dados" / "mock" / "operacional" / nome,
            Path("/workspace") / "dados" / "mock" / "operacional" / nome,
        ]
        for p in candidatos:
            if p.exists():
                return p
        return None

    candidatas_path = _encontrar_dados("candidatas.json")
    clientes_path = _encontrar_dados("clientes.json")
    historico_path = _encontrar_dados("historico.json")

    if candidatas_path is None:
        cwd = Path(os.getcwd())
        script_dir = Path(__file__).parent
        return {
            "status": "erro",
            "mensagem": "Dados operacionais nao encontrados.",
            "cwd": str(cwd),
            "script_dir": str(script_dir),
            "dica": "Verifique se dados/mock/operacional/ esta no ZIP da ferramenta.",
        }

    try:
        candidatas = json.loads(candidatas_path.read_bytes())
        tx = next(
            (
                c
                for c in candidatas
                if c.get("id") == transacao_id or c.get("event_external_id") == transacao_id
            ),
            None,
        )
    except Exception as exc:
        return {"status": "erro", "mensagem": f"Falha ao ler candidatas: {exc}"}

    if tx is None:
        return {"status": "erro", "mensagem": f"Transacao '{transacao_id}' nao encontrada."}

    # Remove campo interno roteiro_cliente (nunca exposto ao agente)
    tx_limpa = {k: v for k, v in tx.items() if k != "roteiro_cliente"}

    cliente_id = tx.get("customer_id") or tx.get("cliente_id", "")
    cliente: dict = {}
    if clientes_path and clientes_path.exists():
        try:
            clientes = json.loads(clientes_path.read_bytes())
            cliente = next((c for c in clientes if c.get("customer_id") == cliente_id), {})
        except Exception:
            pass

    historico_resumo: dict = {}
    if historico_path and historico_path.exists():
        try:
            historico = json.loads(historico_path.read_bytes())
            hist_cliente = [
                h
                for h in historico
                if h.get("customer_id") == cliente_id or h.get("cliente_id") == cliente_id
            ]
            if hist_cliente:
                valores = [
                    h.get("amount") or h.get("valor", 0)
                    for h in hist_cliente
                    if h.get("amount") or h.get("valor")
                ]
                from collections import Counter

                cats = Counter(h.get("categoria", "") for h in hist_cliente if h.get("categoria"))
                estabs = Counter(
                    h.get("acceptor_name") or h.get("estabelecimento", {}).get("nome", "")
                    for h in hist_cliente
                )
                historico_resumo = {
                    "total_transacoes": len(hist_cliente),
                    "ticket_medio": round(sum(valores) / len(valores), 2) if valores else None,
                    "categorias_frequentes": [k for k, _ in cats.most_common(5)],
                    "estabelecimentos_frequentes": [k for k, _ in estabs.most_common(5)],
                }
        except Exception:
            pass

    # Projeta a transação para o agente: remove aliases duplicados, preserva sinais
    tx_projetada = _projetar_transacao(tx_limpa)

    return {
        "status": "ok",
        "transacao": tx_projetada,
        "cliente": {
            "customer_id": cliente.get("customer_id"),
            "customer_name": cliente.get("customer_name"),
            "customer_city": cliente.get("customer_city"),
            "customer_region": cliente.get("customer_region"),
            "customer_country_code": cliente.get("customer_country_code"),
            "customer_risk_rating": cliente.get("customer_risk_rating"),
            "customer_segment": cliente.get("customer_segment"),
            "perfil_gasto": cliente.get("perfil_gasto"),
        },
        "cartao": {
            "card_brand": tx.get("card_brand"),
            "card_last4": tx.get("card_last4"),
            "card_type": tx.get("card_type"),
            "card_credit_limit": tx.get("card_credit_limit"),
            "card_presence_flag": tx.get("card_presence_flag"),
            "card_status": tx.get("card_status"),
        },
        "historico_resumo": historico_resumo,
        "eventos_recentes": tx.get("eventos_recentes", []),
    }


def _projetar_transacao(tx: dict) -> dict:
    """
    Produz uma projeção da transação para o agente, removendo apenas aliases
    com valor idêntico a outro campo já presente.

    Campos removidos (e seu equivalente preservado):
      - event_external_id (valor == id)
      - local_datetime    (valor == event_occurred_at)
      - cliente_id        (valor == customer_id)
      - moeda             (valor == currency_code; /valor e /currency_code preservados)
      - data_hora         (valor == event_occurred_at)
      - customer_name, customer_city, customer_region,
        customer_risk_rating, customer_segment → bloco cliente no retorno

    Campos preservados (não são aliases puros):
      - customer_country_code: país do titular (BR). Diverge de acceptor_country_code
        quando há compras internacionais — sinal de risco geográfico.
      - card_id, card_brand, card_last4, card_type, card_credit_limit,
        card_presence_flag, card_status → bloco cartao no retorno MAS também
        preservados na transação para que /card_status seja referenciável
        como caminho de evidência (ex: cartão bloqueado).
      - device_fingerprint: diverge de device_id em todos os registros testados
        (device_id=DEV_SYN_..., fingerprint=FP_SYN_...). São sinais distintos.
    """
    aliases = {
        "event_external_id": "id",
        "local_datetime": "event_occurred_at",
        "cliente_id": "customer_id",
        "moeda": "currency_code",
        "data_hora": "event_occurred_at",
    }
    return {
        k: v
        for k, v in tx.items()
        if k not in aliases or aliases[k] not in tx or v != tx[aliases[k]]
    }
