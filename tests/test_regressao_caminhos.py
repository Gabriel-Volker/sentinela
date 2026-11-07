"""
tests/test_regressao_caminhos.py — Testes de regressão para caminhos de evidência.

Documenta e verifica:
 1. Que /transacao/<campo> NÃO é aceito pela ferramenta registrar_analise
    (o contrato opera na raiz da transação, não em um objeto "transacao" aninhado).
 2. Que caminhos raiz /<campo> SÃO aceitos.
 3. Que caminhos aninhados reais (ex: /estabelecimento/nome) são aceitos.
 4. Que caminhos inventados continuam sendo rejeitados.
 5. Que as instruções dos YAMLs usam caminhos corretos (sem prefixo /transacao/).
 6. Que os exemplos JSON dos prompts não contêm chaves duplas {{ ou }}.
 7. Que o contexto reduzido preserva campos necessários para evidências.
 8. Que registrar_analise valida o pointer completo, não só o primeiro segmento.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from fraude.contratos import resolver_pointer

# ---------------------------------------------------------------------------
# Transação de referência para os testes
# ---------------------------------------------------------------------------

_BASE = Path(__file__).parent.parent / "dados" / "mock" / "operacional"


def _tx_referencia() -> dict:
    """Carrega a primeira transação candidata para uso nos testes."""
    p = _BASE / "candidatas.json"
    if not p.exists():
        pytest.skip("Dados operacionais não encontrados — execute scripts/gerar_dados.py")
    data = json.loads(p.read_bytes())
    if not data:
        pytest.skip("candidatas.json vazio")
    return data[0]


# ---------------------------------------------------------------------------
# 1. PROBLEMA CENTRAL: /transacao/<campo> é REJEITADO
# ---------------------------------------------------------------------------


def test_caminho_com_prefixo_transacao_e_rejeitado():
    """/transacao/valor deve ser rejeitado — a transação não tem objeto 'transacao' na raiz."""
    tx = _tx_referencia()
    with pytest.raises(ValueError, match="não encontrado|Caminho"):
        resolver_pointer(tx, "/transacao/valor")


def test_caminho_com_prefixo_transacao_campo_existente_rejeitado():
    """/transacao/amount também deve ser rejeitado — o prefixo /transacao/ é inválido."""
    tx = _tx_referencia()
    with pytest.raises(ValueError):
        resolver_pointer(tx, "/transacao/amount")


def test_caminho_com_prefixo_transacao_estabelecimento_rejeitado():
    """/transacao/estabelecimento/nome deve ser rejeitado."""
    tx = _tx_referencia()
    with pytest.raises(ValueError):
        resolver_pointer(tx, "/transacao/estabelecimento/nome")


# ---------------------------------------------------------------------------
# 2. Caminhos raiz CORRETOS são aceitos
# ---------------------------------------------------------------------------


def test_caminho_raiz_valor_aceito():
    tx = _tx_referencia()
    v = resolver_pointer(tx, "/valor")
    assert v is not None


def test_caminho_raiz_amount_aceito():
    tx = _tx_referencia()
    v = resolver_pointer(tx, "/amount")
    assert isinstance(v, (int, float))


def test_caminho_raiz_canal_aceito():
    tx = _tx_referencia()
    v = resolver_pointer(tx, "/canal")
    assert isinstance(v, str)


def test_caminho_raiz_customer_risk_rating_aceito():
    tx = _tx_referencia()
    v = resolver_pointer(tx, "/customer_risk_rating")
    assert isinstance(v, str)


def test_caminho_aninhado_estabelecimento_nome_aceito():
    tx = _tx_referencia()
    v = resolver_pointer(tx, "/estabelecimento/nome")
    assert isinstance(v, str)


def test_caminho_aninhado_estabelecimento_categoria_aceito():
    tx = _tx_referencia()
    v = resolver_pointer(tx, "/estabelecimento/categoria")
    assert isinstance(v, str)


def test_caminho_aninhado_dispositivo_novo_aceito():
    tx = _tx_referencia()
    v = resolver_pointer(tx, "/dispositivo/novo")
    assert isinstance(v, bool)


# ---------------------------------------------------------------------------
# 3. Caminhos inventados continuam sendo rejeitados
# ---------------------------------------------------------------------------


def test_caminho_inventado_fraude_score_rejeitado():
    tx = _tx_referencia()
    with pytest.raises(ValueError):
        resolver_pointer(tx, "/fraude_score")


def test_caminho_inventado_localizacao_divergente_rejeitado():
    tx = _tx_referencia()
    with pytest.raises(ValueError):
        resolver_pointer(tx, "/divergencia_geografica")


def test_caminho_inventado_aninhado_rejeitado():
    tx = _tx_referencia()
    with pytest.raises(ValueError):
        resolver_pointer(tx, "/estabelecimento/campo_inexistente")


# ---------------------------------------------------------------------------
# 4. registrar_analise valida pointer completo (não só primeiro segmento)
# ---------------------------------------------------------------------------


def test_registrar_analise_rejeita_prefixo_transacao():
    """
    A ferramenta registrar_analise deve rejeitar /transacao/valor.
    Este teste documenta o comportamento ESPERADO após a correção.
    Antes da correção, ela verificava apenas o primeiro segmento ('transacao' não existe → erro).
    Depois da correção, usa resolver_pointer completo.
    """
    import sys
    import types

    # Stub do ibm_watsonx_orchestrate se não estiver instalado
    if "ibm_watsonx_orchestrate" not in sys.modules:
        stub_pkg = types.ModuleType("ibm_watsonx_orchestrate")
        stub_ab = types.ModuleType("ibm_watsonx_orchestrate.agent_builder")
        stub_tools = types.ModuleType("ibm_watsonx_orchestrate.agent_builder.tools")
        stub_tool_mod = types.ModuleType("ibm_watsonx_orchestrate.agent_builder.tools.python_tool")
        stub_tool_mod.tool = lambda **kw: lambda f: f
        stub_pkg.agent_builder = stub_ab
        stub_ab.tools = stub_tools
        stub_tools.python_tool = stub_tool_mod
        sys.modules["ibm_watsonx_orchestrate"] = stub_pkg
        sys.modules["ibm_watsonx_orchestrate.agent_builder"] = stub_ab
        sys.modules["ibm_watsonx_orchestrate.agent_builder.tools"] = stub_tools
        sys.modules["ibm_watsonx_orchestrate.agent_builder.tools.python_tool"] = stub_tool_mod

    from ferramentas.analise import registrar_analise

    tx = _tx_referencia()
    tx_id = tx.get("id") or tx.get("event_external_id")

    analise_json = json.dumps(
        {
            "versao": 2,
            "transacao_id": tx_id,
            "risco": "baixo",
            "acao": "autorizar",
            "evidencias": [{"caminho": "/transacao/valor", "descricao": "Valor incorreto."}],
            "informacoes_ausentes": [],
            "justificativa": "Teste de caminho inválido.",
        }
    )
    resultado = registrar_analise(tx_id, analise_json)
    # Deve retornar erro de caminho, não ok
    assert resultado["status"] == "erro", (
        f"registrar_analise deveria rejeitar /transacao/valor, mas retornou: {resultado}"
    )


def test_registrar_analise_aceita_caminho_raiz():
    """registrar_analise deve aceitar /valor (caminho raiz correto)."""
    import sys
    import types

    if "ibm_watsonx_orchestrate" not in sys.modules:
        stub_pkg = types.ModuleType("ibm_watsonx_orchestrate")
        stub_ab = types.ModuleType("ibm_watsonx_orchestrate.agent_builder")
        stub_tools = types.ModuleType("ibm_watsonx_orchestrate.agent_builder.tools")
        stub_tool_mod = types.ModuleType("ibm_watsonx_orchestrate.agent_builder.tools.python_tool")
        stub_tool_mod.tool = lambda **kw: lambda f: f
        stub_pkg.agent_builder = stub_ab
        stub_ab.tools = stub_tools
        stub_tools.python_tool = stub_tool_mod
        sys.modules["ibm_watsonx_orchestrate"] = stub_pkg
        sys.modules["ibm_watsonx_orchestrate.agent_builder"] = stub_ab
        sys.modules["ibm_watsonx_orchestrate.agent_builder.tools"] = stub_tools
        sys.modules["ibm_watsonx_orchestrate.agent_builder.tools.python_tool"] = stub_tool_mod

    from ferramentas.analise import registrar_analise

    tx = _tx_referencia()
    tx_id = tx.get("id") or tx.get("event_external_id")

    analise_json = json.dumps(
        {
            "versao": 2,
            "transacao_id": tx_id,
            "risco": "baixo",
            "acao": "autorizar",
            "evidencias": [{"caminho": "/valor", "descricao": "Valor da transação."}],
            "informacoes_ausentes": [],
            "justificativa": "Teste de caminho correto.",
        }
    )
    resultado = registrar_analise(tx_id, analise_json)
    assert resultado["status"] == "ok", f"Esperado ok, recebido: {resultado}"


def test_registrar_analise_aceita_caminho_aninhado():
    """registrar_analise deve aceitar /estabelecimento/nome (pointer de 2 níveis)."""
    import sys
    import types

    if "ibm_watsonx_orchestrate" not in sys.modules:
        stub_pkg = types.ModuleType("ibm_watsonx_orchestrate")
        stub_ab = types.ModuleType("ibm_watsonx_orchestrate.agent_builder")
        stub_tools = types.ModuleType("ibm_watsonx_orchestrate.agent_builder.tools")
        stub_tool_mod = types.ModuleType("ibm_watsonx_orchestrate.agent_builder.tools.python_tool")
        stub_tool_mod.tool = lambda **kw: lambda f: f
        stub_pkg.agent_builder = stub_ab
        stub_ab.tools = stub_tools
        stub_tools.python_tool = stub_tool_mod
        sys.modules["ibm_watsonx_orchestrate"] = stub_pkg
        sys.modules["ibm_watsonx_orchestrate.agent_builder"] = stub_ab
        sys.modules["ibm_watsonx_orchestrate.agent_builder.tools"] = stub_tools
        sys.modules["ibm_watsonx_orchestrate.agent_builder.tools.python_tool"] = stub_tool_mod

    from ferramentas.analise import registrar_analise

    tx = _tx_referencia()
    tx_id = tx.get("id") or tx.get("event_external_id")

    analise_json = json.dumps(
        {
            "versao": 2,
            "transacao_id": tx_id,
            "risco": "baixo",
            "acao": "autorizar",
            "evidencias": [
                {"caminho": "/estabelecimento/nome", "descricao": "Nome do estabelecimento."}
            ],
            "informacoes_ausentes": [],
            "justificativa": "Teste de pointer aninhado.",
        }
    )
    resultado = registrar_analise(tx_id, analise_json)
    assert resultado["status"] == "ok", f"Esperado ok, recebido: {resultado}"


def test_registrar_analise_rejeita_objeto_nivel1():
    """registrar_analise deve rejeitar /estabelecimento (aponta para objeto, não escalar)."""
    import sys
    import types

    if "ibm_watsonx_orchestrate" not in sys.modules:
        stub_pkg = types.ModuleType("ibm_watsonx_orchestrate")
        stub_ab = types.ModuleType("ibm_watsonx_orchestrate.agent_builder")
        stub_tools = types.ModuleType("ibm_watsonx_orchestrate.agent_builder.tools")
        stub_tool_mod = types.ModuleType("ibm_watsonx_orchestrate.agent_builder.tools.python_tool")
        stub_tool_mod.tool = lambda **kw: lambda f: f
        stub_pkg.agent_builder = stub_ab
        stub_ab.tools = stub_tools
        stub_tools.python_tool = stub_tool_mod
        sys.modules["ibm_watsonx_orchestrate"] = stub_pkg
        sys.modules["ibm_watsonx_orchestrate.agent_builder"] = stub_ab
        sys.modules["ibm_watsonx_orchestrate.agent_builder.tools"] = stub_tools
        sys.modules["ibm_watsonx_orchestrate.agent_builder.tools.python_tool"] = stub_tool_mod

    from ferramentas.analise import registrar_analise

    tx = _tx_referencia()
    tx_id = tx.get("id") or tx.get("event_external_id")

    analise_json = json.dumps(
        {
            "versao": 2,
            "transacao_id": tx_id,
            "risco": "baixo",
            "acao": "autorizar",
            "evidencias": [
                {"caminho": "/estabelecimento", "descricao": "Objeto completo — inválido."}
            ],
            "informacoes_ausentes": [],
            "justificativa": "Teste de rejeição de objeto.",
        }
    )
    resultado = registrar_analise(tx_id, analise_json)
    assert resultado["status"] == "erro", (
        f"registrar_analise deveria rejeitar /estabelecimento (objeto), recebeu: {resultado}"
    )


# ---------------------------------------------------------------------------
# 5. Prompts YAML não devem conter prefixo /transacao/ nas evidências
# ---------------------------------------------------------------------------

_YAML_ANALISE = Path(__file__).parent.parent / "agentes" / "analise.yaml"
_YAML_VALIDACAO = Path(__file__).parent.parent / "agentes" / "validacao.yaml"


def test_yaml_analise_sem_prefixo_transacao_em_evidencias():
    """
    O YAML do agente de análise não deve instruir caminhos como /transacao/<campo>.
    Esses caminhos são rejeitados pela ferramenta registrar_analise.
    """
    if not _YAML_ANALISE.exists():
        pytest.skip("agentes/analise.yaml não encontrado")
    conteudo = _YAML_ANALISE.read_text(encoding="utf-8")
    # Procura por exemplos de evidência com /transacao/ no caminho
    matches = re.findall(r'"caminho":\s*"/transacao/', conteudo)
    assert not matches, (
        f"analise.yaml contém {len(matches)} exemplo(s) com caminho '/transacao/...' "
        "que são rejeitados pela ferramenta registrar_analise. "
        "Use '/valor', '/canal', '/estabelecimento/nome', etc."
    )


def test_yaml_validacao_sem_prefixo_transacao_em_evidencias():
    """O YAML do agente de validação não deve instruir caminhos /transacao/<campo>."""
    if not _YAML_VALIDACAO.exists():
        pytest.skip("agentes/validacao.yaml não encontrado")
    conteudo = _YAML_VALIDACAO.read_text(encoding="utf-8")
    matches = re.findall(r'"caminho":\s*"/transacao/', conteudo)
    assert not matches, (
        f"validacao.yaml contém {len(matches)} exemplo(s) com caminho '/transacao/...' "
        "que são rejeitados pela ferramenta."
    )


# ---------------------------------------------------------------------------
# 6. Exemplos JSON dos prompts não devem conter chaves duplas {{ ou }}
# ---------------------------------------------------------------------------


def test_yaml_analise_sem_chaves_duplas():
    """
    No YAML (não f-string), {{ e }} são literais, não escapes de chaves.
    O modelo recebe '{{' e '}}'  ao invés de '{' e '}' — JSON inválido.
    """
    if not _YAML_ANALISE.exists():
        pytest.skip("agentes/analise.yaml não encontrado")
    conteudo = _YAML_ANALISE.read_text(encoding="utf-8")
    assert "{{" not in conteudo, "analise.yaml contém '{{' — JSON inválido para o modelo."
    assert "}}" not in conteudo, "analise.yaml contém '}}' — JSON inválido para o modelo."


def test_yaml_validacao_sem_chaves_duplas():
    """O YAML do agente de validação não deve conter chaves duplas."""
    if not _YAML_VALIDACAO.exists():
        pytest.skip("agentes/validacao.yaml não encontrado")
    conteudo = _YAML_VALIDACAO.read_text(encoding="utf-8")
    assert "{{" not in conteudo, "validacao.yaml contém '{{' — JSON inválido para o modelo."
    assert "}}" not in conteudo, "validacao.yaml contém '}}' — JSON inválido para o modelo."


# ---------------------------------------------------------------------------
# 7. Exemplos JSON dos prompts do coordenador não devem ter chaves duplas
# ---------------------------------------------------------------------------


def test_prompt_analise_sem_chaves_duplas():
    """_prompt_analise usa f-string: {{...}} → {...} correto. Verifica que o texto gerado não tem chaves duplas."""
    from fraude.coordenador import _prompt_analise

    prompt = _prompt_analise("TX-TESTE123")
    assert "{{" not in prompt, "Prompt de análise contém '{{' — provável problema de escape."
    assert "}}" not in prompt, "Prompt de análise contém '}}' — provável problema de escape."


def test_prompt_validacao_sem_chaves_duplas():
    """_prompt_validacao também deve gerar texto sem chaves duplas."""
    from fraude.coordenador import _prompt_validacao

    prompt = _prompt_validacao("TX-TESTE123", [])
    assert "{{" not in prompt, "Prompt de validação (1.º turno) contém '{{'."
    assert "}}" not in prompt, "Prompt de validação (1.º turno) contém '}}'."

    # Turno com histórico
    conversa = [{"papel": "agente", "texto": "Você reconhece?"}]
    prompt2 = _prompt_validacao("TX-TESTE123", conversa)
    assert "{{" not in prompt2, "Prompt de validação (turnos) contém '{{'."


# ---------------------------------------------------------------------------
# 8. Contexto reduzido preserva campos de evidência esperados
# ---------------------------------------------------------------------------


def test_contexto_reduzido_preserva_campos_essenciais():
    """
    A projeção do contexto enviado ao agente deve manter os campos
    que o agente usa como caminhos de evidência.
    """
    import sys
    import types

    if "ibm_watsonx_orchestrate" not in sys.modules:
        stub_pkg = types.ModuleType("ibm_watsonx_orchestrate")
        stub_ab = types.ModuleType("ibm_watsonx_orchestrate.agent_builder")
        stub_tools = types.ModuleType("ibm_watsonx_orchestrate.agent_builder.tools")
        stub_tool_mod = types.ModuleType("ibm_watsonx_orchestrate.agent_builder.tools.python_tool")
        stub_tool_mod.tool = lambda **kw: lambda f: f
        stub_pkg.agent_builder = stub_ab
        stub_ab.tools = stub_tools
        stub_tools.python_tool = stub_tool_mod
        sys.modules["ibm_watsonx_orchestrate"] = stub_pkg
        sys.modules["ibm_watsonx_orchestrate.agent_builder"] = stub_ab
        sys.modules["ibm_watsonx_orchestrate.agent_builder.tools"] = stub_tools
        sys.modules["ibm_watsonx_orchestrate.agent_builder.tools.python_tool"] = stub_tool_mod

    from ferramentas.contexto import consultar_contexto

    tx = _tx_referencia()
    tx_id = tx.get("id") or tx.get("event_external_id")
    ctx = consultar_contexto(tx_id)

    assert ctx.get("status") == "ok", f"consultar_contexto falhou: {ctx}"

    tx_retornada = ctx.get("transacao", {})
    # Campos que os agentes usam como evidência
    campos_essenciais = ["valor", "canal", "estabelecimento", "dispositivo"]
    for campo in campos_essenciais:
        assert campo in tx_retornada, (
            f"Campo '{campo}' ausente em transacao no contexto — "
            "agentes não conseguirão criar evidências válidas."
        )


def test_contexto_nao_expoe_roteiro():
    """O contexto não deve expor roteiro_cliente."""
    import sys
    import types

    if "ibm_watsonx_orchestrate" not in sys.modules:
        stub_pkg = types.ModuleType("ibm_watsonx_orchestrate")
        stub_ab = types.ModuleType("ibm_watsonx_orchestrate.agent_builder")
        stub_tools = types.ModuleType("ibm_watsonx_orchestrate.agent_builder.tools")
        stub_tool_mod = types.ModuleType("ibm_watsonx_orchestrate.agent_builder.tools.python_tool")
        stub_tool_mod.tool = lambda **kw: lambda f: f
        stub_pkg.agent_builder = stub_ab
        stub_ab.tools = stub_tools
        stub_tools.python_tool = stub_tool_mod
        sys.modules["ibm_watsonx_orchestrate"] = stub_pkg
        sys.modules["ibm_watsonx_orchestrate.agent_builder"] = stub_ab
        sys.modules["ibm_watsonx_orchestrate.agent_builder.tools"] = stub_tools
        sys.modules["ibm_watsonx_orchestrate.agent_builder.tools.python_tool"] = stub_tool_mod

    from ferramentas.contexto import consultar_contexto

    tx = _tx_referencia()
    tx_id = tx.get("id") or tx.get("event_external_id")
    ctx = consultar_contexto(tx_id)
    assert "roteiro_cliente" not in ctx.get("transacao", {}), (
        "roteiro_cliente não deve ser exposto ao agente."
    )
