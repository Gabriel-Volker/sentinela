"""
tests/test_contratos.py — Testes dos contratos e validações centrais.

Deterministico, sem credenciais, sem IBM.
"""

import json

import pytest

from fraude.contratos import (
    DESFECHOS_FINAIS,
    RISCO_ACAO,
    AcaoAnalise,
    DesfechoValidacao,
    Evidencia,
    NivelRisco,
    RespostaAnalise,
    RespostaValidacao,
    calcular_hash,
    parsear_analise,
    resolver_pointer,
    validar_evidencias,
)

# ---------------------------------------------------------------------------
# Correspondência risco → ação
# ---------------------------------------------------------------------------


def test_correspondencias_obrigatorias():
    assert RISCO_ACAO[NivelRisco.BAIXO] == AcaoAnalise.AUTORIZAR
    assert RISCO_ACAO[NivelRisco.MEDIO] == AcaoAnalise.VALIDAR
    assert RISCO_ACAO[NivelRisco.ALTO] == AcaoAnalise.BLOQUEAR


def test_desfechos_finais():
    assert DesfechoValidacao.AUTORIZAR in DESFECHOS_FINAIS
    assert DesfechoValidacao.BLOQUEAR in DESFECHOS_FINAIS
    assert DesfechoValidacao.PENDENTE in DESFECHOS_FINAIS
    assert DesfechoValidacao.AGUARDAR_CLIENTE not in DESFECHOS_FINAIS


# ---------------------------------------------------------------------------
# RespostaAnalise válida
# ---------------------------------------------------------------------------


def _analise_valida(**kwargs) -> dict:
    base = {
        "versao": 2,
        "transacao_id": "TX-ABC123",
        "risco": "baixo",
        "acao": "autorizar",
        "evidencias": [{"caminho": "/valor", "descricao": "Valor da compra."}],
        "informacoes_ausentes": [],
        "justificativa": "Perfil habitual.",
    }
    base.update(kwargs)
    return base


def test_analise_valida_baixo():
    a = RespostaAnalise.model_validate(_analise_valida())
    assert a.risco == NivelRisco.BAIXO
    assert a.acao == AcaoAnalise.AUTORIZAR


def test_analise_valida_medio():
    d = _analise_valida(risco="medio", acao="validar")
    a = RespostaAnalise.model_validate(d)
    assert a.risco == NivelRisco.MEDIO


def test_analise_valida_alto():
    d = _analise_valida(risco="alto", acao="bloquear")
    a = RespostaAnalise.model_validate(d)
    assert a.risco == NivelRisco.ALTO


def test_analise_acao_incompativel_com_risco():
    d = _analise_valida(risco="baixo", acao="bloquear")
    with pytest.raises(Exception, match="incompatível"):
        RespostaAnalise.model_validate(d)


def test_analise_versao_errada():
    d = _analise_valida(versao=1)
    with pytest.raises(ValueError):
        RespostaAnalise.model_validate(d)


def test_analise_exige_versao():
    d = _analise_valida()
    del d["versao"]
    with pytest.raises(ValueError):
        RespostaAnalise.model_validate(d)


def test_analise_sem_evidencias():
    d = _analise_valida(evidencias=[])
    with pytest.raises(ValueError):
        RespostaAnalise.model_validate(d)


def test_analise_campo_extra_rejeitado():
    d = _analise_valida()
    d["campo_inexistente"] = "x"
    with pytest.raises(ValueError):
        RespostaAnalise.model_validate(d)


def test_analise_justificativa_vazia():
    d = _analise_valida(justificativa="  ")
    with pytest.raises(ValueError):
        RespostaAnalise.model_validate(d)


def test_parsear_analise_json_string():
    d = _analise_valida()
    a = parsear_analise(json.dumps(d))
    assert a.transacao_id == "TX-ABC123"


def test_parsear_analise_json_invalido():
    with pytest.raises(ValueError, match="JSON inválido"):
        parsear_analise("{invalido}")


# ---------------------------------------------------------------------------
# RespostaValidacao
# ---------------------------------------------------------------------------


def _validacao_valida(**kwargs) -> dict:
    base = {
        "versao": 2,
        "transacao_id": "TX-ABC123",
        "desfecho": "aguardar_cliente",
        "pergunta": "Você reconhece esta compra?",
        "evidencias": [{"caminho": "/valor", "descricao": "Valor apresentado."}],
        "justificativa": "Necessário confirmar.",
    }
    base.update(kwargs)
    return base


def test_validacao_exige_versao():
    d = _validacao_valida()
    del d["versao"]
    with pytest.raises(ValueError):
        RespostaValidacao.model_validate(d)


def test_validacao_aguardar_cliente():
    v = RespostaValidacao.model_validate(_validacao_valida())
    assert v.desfecho == DesfechoValidacao.AGUARDAR_CLIENTE
    assert v.pergunta is not None


def test_validacao_autorizar_sem_pergunta():
    d = _validacao_valida(desfecho="autorizar", pergunta=None)
    v = RespostaValidacao.model_validate(d)
    assert v.desfecho == DesfechoValidacao.AUTORIZAR
    assert v.pergunta is None


def test_validacao_bloquear_sem_pergunta():
    d = _validacao_valida(desfecho="bloquear", pergunta=None)
    v = RespostaValidacao.model_validate(d)
    assert v.desfecho == DesfechoValidacao.BLOQUEAR


def test_validacao_pendente_sem_pergunta():
    d = _validacao_valida(desfecho="pendente", pergunta=None)
    v = RespostaValidacao.model_validate(d)
    assert v.desfecho == DesfechoValidacao.PENDENTE


def test_desfecho_final_com_pergunta_rejeitado():
    d = _validacao_valida(desfecho="autorizar", pergunta="Confirma?")
    with pytest.raises(Exception, match="pergunta"):
        RespostaValidacao.model_validate(d)


def test_aguardar_cliente_sem_pergunta_rejeitado():
    d = _validacao_valida(desfecho="aguardar_cliente", pergunta=None)
    with pytest.raises(Exception, match="pergunta"):
        RespostaValidacao.model_validate(d)


def test_validacao_campo_extra_rejeitado():
    d = _validacao_valida()
    d["campo_extra"] = "boom"
    with pytest.raises(ValueError):
        RespostaValidacao.model_validate(d)


# ---------------------------------------------------------------------------
# JSON Pointer e evidências
# ---------------------------------------------------------------------------


TX_TESTE = {
    "id": "TX-ABC123",
    "valor": 250.00,
    "moeda": "BRL",
    "estabelecimento": {
        "nome": "Carrefour",
        "categoria": "supermercado",
    },
    "localizacao": {
        "cidade": "São Paulo",
        "pais": "BR",
    },
}


def test_resolver_pointer_escalar():
    assert resolver_pointer(TX_TESTE, "/valor") == 250.00


def test_resolver_pointer_aninhado():
    assert resolver_pointer(TX_TESTE, "/estabelecimento/nome") == "Carrefour"


def test_resolver_pointer_inexistente():
    with pytest.raises(ValueError, match="não encontrado"):
        resolver_pointer(TX_TESTE, "/campo_inexistente")


def test_resolver_pointer_objeto_rejeitado():
    with pytest.raises(ValueError, match="objeto/lista"):
        resolver_pointer(TX_TESTE, "/estabelecimento")


def test_resolver_pointer_sem_barra():
    with pytest.raises(ValueError):
        resolver_pointer(TX_TESTE, "valor")


def test_validar_evidencias_ok():
    evs = [Evidencia(caminho="/valor", descricao="Valor")]
    resultado = validar_evidencias(evs, TX_TESTE)
    assert resultado[0].valor == 250.00


def test_validar_evidencias_caminho_invalido():
    evs = [Evidencia(caminho="/inexistente", descricao="X")]
    with pytest.raises(ValueError):
        validar_evidencias(evs, TX_TESTE)


def test_validar_evidencias_objeto_rejeitado():
    evs = [Evidencia(caminho="/estabelecimento", descricao="Objeto inteiro")]
    with pytest.raises(ValueError):
        validar_evidencias(evs, TX_TESTE)


# ---------------------------------------------------------------------------
# Hash determinístico
# ---------------------------------------------------------------------------


def test_calcular_hash_determinístico():
    payload = {"a": 1, "b": "x"}
    h1 = calcular_hash(payload)
    h2 = calcular_hash(payload)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex


def test_calcular_hash_diferente_para_payloads_distintos():
    h1 = calcular_hash({"risco": "baixo"})
    h2 = calcular_hash({"risco": "alto"})
    assert h1 != h2
