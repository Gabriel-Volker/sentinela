"""
ferramentas/analise.py — Ferramenta remota: registrar_analise.

Recebe a análise estruturada do agente, valida o contrato e retorna
um comprovante verificável.
"""

from __future__ import annotations


from ibm_watsonx_orchestrate.agent_builder.tools.python_tool import tool


@tool(
    name="registrar_analise",
    description=(
        "Registra a analise de risco estruturada da transacao. "
        "Deve ser chamada apos consultar_contexto e antes de encerrar a execucao. "
        "Recebe o JSON de analise conforme o contrato versao 2 e o ID da transacao. "
        "Retorna um comprovante verificavel da analise registrada."
    ),
)
def registrar_analise(transacao_id: str, analise_json: str) -> dict:
    """
    Registra a análise estruturada de risco.

    Args:
        transacao_id: Identificador da transação analisada (ex: TX-XXXXXXXX).
        analise_json: JSON da analise conforme o contrato versao 2 do Sentinela.
            Deve conter: versao, transacao_id, risco, acao, evidencias, justificativa.
            Os caminhos de evidencia sao JSON Pointers diretos na transacao
            (ex: /valor, /canal, /estabelecimento/nome). NAO use /transacao/campo.

    Returns:
        Comprovante verificavel com transacao_id, risco, acao e hash.
    """
    from fraude.contratos import parsear_analise, calcular_hash, validar_evidencias
    from fraude.repositorio import buscar_transacao

    if not isinstance(transacao_id, str) or not transacao_id.strip():
        return {"status": "erro", "mensagem": "transacao_id e obrigatorio."}
    transacao_id = transacao_id.strip()
    try:
        resposta = parsear_analise(analise_json)
        if resposta.transacao_id != transacao_id:
            raise ValueError("transacao_id divergente.")
        tx = buscar_transacao(transacao_id)
        if tx is None:
            raise ValueError("Transacao nao encontrada nos dados operacionais.")
        validar_evidencias(resposta.evidencias, tx)
    except (ValueError, TypeError, OSError):
        return {
            "status": "erro",
            "mensagem": "Contrato, transacao, evidencia ou dados operacionais invalidos.",
        }

    return {
        "status": "ok",
        "transacao_id": transacao_id,
        "fase": "analise",
        "risco": resposta.risco.value,
        "acao": resposta.acao.value,
        "hash_analise": calcular_hash(resposta.model_dump()),
    }
