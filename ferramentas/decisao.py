"""
ferramentas/decisao.py — Ferramenta remota: executar_decisao.

Recebe a resposta estruturada do agente de validação, valida e retorna
uma solicitação de decisão simulada verificável.
Não acessa bancos reais.
"""

from __future__ import annotations


from ibm_watsonx_orchestrate.agent_builder.tools.python_tool import tool


@tool(
    name="executar_decisao",
    description=(
        "Executa a decisao final sobre a transacao em analise. "
        "Deve ser chamada apenas quando o desfecho for definitivo (autorizar, bloquear ou pendente). "
        "Recebe o JSON de validacao conforme o contrato versao 2 e o ID da transacao. "
        "Retorna uma solicitacao de decisao simulada verificavel. "
        "Nao acessa sistemas bancarios reais."
    ),
)
def executar_decisao(transacao_id: str, validacao_json: str) -> dict:
    """
    Registra a decisão final do agente de validação.

    Args:
        transacao_id: Identificador da transação (ex: TX-XXXXXXXX).
        validacao_json: JSON da validacao conforme o contrato versao 2 do Sentinela.
            Deve conter: versao, transacao_id, desfecho (autorizar/bloquear/pendente),
            evidencias e justificativa.

    Returns:
        Solicitacao de decisao simulada verificavel com comprovante.
    """
    from fraude.contratos import parsear_validacao, calcular_hash, validar_evidencias
    from fraude.repositorio import buscar_transacao

    if not isinstance(transacao_id, str) or not transacao_id.strip():
        return {"status": "erro", "mensagem": "transacao_id e obrigatorio."}
    transacao_id = transacao_id.strip()
    try:
        resposta = parsear_validacao(validacao_json)
        if resposta.transacao_id != transacao_id:
            raise ValueError("transacao_id divergente.")
        tx = buscar_transacao(transacao_id)
        if tx is None:
            raise ValueError("Transacao nao encontrada nos dados operacionais.")
        validar_evidencias(resposta.evidencias, tx)
        if resposta.desfecho.value == "aguardar_cliente":
            raise ValueError("executar_decisao so aceita desfechos finais.")
    except (ValueError, TypeError, OSError):
        return {
            "status": "erro",
            "mensagem": "Contrato, transacao, evidencia ou dados operacionais invalidos.",
        }

    return {
        "status": "ok",
        "transacao_id": transacao_id,
        "fase": "validacao",
        "desfecho": resposta.desfecho.value,
        "simulado": True,
        "hash_validacao": calcular_hash(resposta.model_dump()),
    }
