"""
tests/test_orchestrate.py — Testes do adaptador IBM Orchestrate.

Verifica a extração de JSON de respostas textuais do Frontier model.
Determinístico, sem credenciais IBM.
"""

from ibm.orchestrate import extrair_json_resposta, extrair_resposta_texto


def _run_com_texto(texto: str) -> dict:
    """Monta um run_status mínimo com texto no caminho result.data.message.content."""
    return {
        "status": "completed",
        "result": {"data": {"message": {"content": [{"response_type": "text", "text": texto}]}}},
    }


# ---------------------------------------------------------------------------
# extrair_resposta_texto
# ---------------------------------------------------------------------------


def test_extrai_texto_de_result_data_message_content():
    run = _run_com_texto("Olá, aqui está o resultado.")
    assert extrair_resposta_texto(run) == "Olá, aqui está o resultado."


def test_extrai_texto_fallback_output():
    run = {"status": "completed", "output": "resposta simples"}
    assert extrair_resposta_texto(run) == "resposta simples"


def test_extrai_texto_retorna_none_sem_conteudo():
    assert extrair_resposta_texto({}) is None
    assert extrair_resposta_texto({"status": "completed"}) is None


# ---------------------------------------------------------------------------
# extrair_json_resposta — JSON puro
# ---------------------------------------------------------------------------


def test_extrai_json_puro():
    payload = {
        "versao": 2,
        "transacao_id": "TX-001",
        "risco": "baixo",
        "acao": "autorizar",
        "evidencias": [],
        "informacoes_ausentes": [],
        "justificativa": "Perfil consistente.",
    }
    import json

    run = _run_com_texto(json.dumps(payload))
    resultado = extrair_json_resposta(run)
    assert resultado is not None
    assert resultado["risco"] == "baixo"
    assert resultado["transacao_id"] == "TX-001"


# ---------------------------------------------------------------------------
# extrair_json_resposta — JSON embutido em narrativa
# ---------------------------------------------------------------------------


def test_extrai_json_de_narrativa_simples():
    import json

    payload = {
        "versao": 2,
        "transacao_id": "TX-002",
        "risco": "alto",
        "acao": "bloquear",
        "evidencias": [{"caminho": "/transacao/valor", "descricao": "Valor elevado"}],
        "informacoes_ausentes": [],
        "justificativa": "Múltiplos indicadores.",
    }
    narrativa = (
        "Análise registrada com sucesso! Aqui está o relatório:\n\n"
        + json.dumps(payload)
        + "\n\nPor favor, verifique o caso acima."
    )
    run = _run_com_texto(narrativa)
    resultado = extrair_json_resposta(run)
    assert resultado is not None
    assert resultado["risco"] == "alto"
    assert resultado["acao"] == "bloquear"


def test_extrai_json_de_narrativa_com_markdown():
    import json

    payload = {
        "versao": 2,
        "transacao_id": "TX-003",
        "risco": "medio",
        "acao": "validar",
        "evidencias": [
            {"caminho": "/transacao/estabelecimento", "descricao": "Novo estabelecimento"}
        ],
        "informacoes_ausentes": ["Reconhecimento do cliente"],
        "justificativa": "Estabelecimento novo para o perfil.",
    }
    narrativa = (
        "## Relatório de Análise\n\n"
        "A análise foi concluída.\n\n"
        "```json\n" + json.dumps(payload, indent=2) + "\n```\n\n"
        "Caso encaminhado para validação."
    )
    run = _run_com_texto(narrativa)
    resultado = extrair_json_resposta(run)
    assert resultado is not None
    assert resultado["risco"] == "medio"
    assert resultado["transacao_id"] == "TX-003"


def test_extrai_json_aninhado_com_evidencias_multiplas():
    """Verifica que o extrator baseado em pilha lida com aninhamento arbitrário."""
    import json

    payload = {
        "versao": 2,
        "transacao_id": "TX-004",
        "risco": "alto",
        "acao": "bloquear",
        "evidencias": [
            {"caminho": "/transacao/valor", "descricao": "Valor muito acima do ticket médio"},
            {"caminho": "/transacao/localizacao/pais", "descricao": "País de alto risco"},
            {
                "caminho": "/transacao/dispositivo/novo",
                "descricao": "Dispositivo novo não reconhecido",
            },
        ],
        "informacoes_ausentes": [],
        "justificativa": "Três indicadores simultâneos de alto risco.",
    }
    run = _run_com_texto("Segue a análise:\n" + json.dumps(payload))
    resultado = extrair_json_resposta(run)
    assert resultado is not None
    assert len(resultado["evidencias"]) == 3
    assert resultado["risco"] == "alto"


def test_retorna_none_sem_json():
    run = _run_com_texto("Não encontrei nenhum JSON nesta resposta.")
    assert extrair_json_resposta(run) is None


def test_retorna_none_json_invalido():
    run = _run_com_texto('{"chave": "valor_sem_fechar}')
    assert extrair_json_resposta(run) is None


def test_prefere_candidato_com_mais_chaves():
    """Quando há múltiplos JSONs no texto, prefere o com mais chaves (contrato completo)."""
    import json

    pequeno = {"ok": True}
    completo = {
        "versao": 2,
        "transacao_id": "TX-005",
        "risco": "baixo",
        "acao": "autorizar",
        "evidencias": [],
        "informacoes_ausentes": [],
        "justificativa": "OK",
    }
    texto = (
        "Ferramenta retornou: " + json.dumps(pequeno) + ". Análise final: " + json.dumps(completo)
    )
    run = _run_com_texto(texto)
    resultado = extrair_json_resposta(run)
    assert resultado is not None
    assert resultado["risco"] == "baixo"
    assert "versao" in resultado


def test_extrai_json_validacao_com_pergunta():
    """Verifica extração de JSON de validação com campo pergunta."""
    import json

    payload = {
        "versao": 2,
        "transacao_id": "TX-006",
        "desfecho": "aguardar_cliente",
        "pergunta": "Você reconhece esta compra de R$ 350,00 na Farmácia São Paulo?",
        "evidencias": [{"caminho": "/transacao/valor", "descricao": "Valor da compra"}],
        "justificativa": "Aguardando confirmação do cliente.",
    }
    run = _run_com_texto("Pergunta enviada: " + json.dumps(payload))
    resultado = extrair_json_resposta(run)
    assert resultado is not None
    assert resultado["desfecho"] == "aguardar_cliente"
    assert resultado["pergunta"] is not None
