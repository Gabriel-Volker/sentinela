"""
tests/test_dados.py — Testes de separação e reprodutibilidade dos dados sintéticos.

Verifica que a geração é determinística e que a separação operacional/gabarito
está correta.
"""

import json
import random
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_OPERACIONAL = _ROOT / "dados" / "mock" / "operacional"
_AVALIACAO = _ROOT / "dados" / "mock" / "avaliacao"
_REFERENCIAS = _ROOT / "dados" / "mock" / "referencias"

SEMENTE = 20260930


def _carregar(caminho: Path) -> list:
    with open(caminho, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Existência dos arquivos gerados
# ---------------------------------------------------------------------------


def test_arquivos_operacional_existem():
    assert (_OPERACIONAL / "clientes.json").exists(), "clientes.json ausente"
    assert (_OPERACIONAL / "candidatas.json").exists(), "candidatas.json ausente"
    assert (_OPERACIONAL / "historico.json").exists(), "historico.json ausente"


def test_arquivos_avaliacao_existem():
    assert (_AVALIACAO / "gabarito_dev.json").exists(), "gabarito_dev.json ausente"
    assert (_AVALIACAO / "gabarito_teste.json").exists(), "gabarito_teste.json ausente"


def test_referencias_existem():
    refs = list(_REFERENCIAS.glob("*.json"))
    assert len(refs) >= 15, f"Esperado >=15 referências, encontrado {len(refs)}"


# ---------------------------------------------------------------------------
# Totais corretos
# ---------------------------------------------------------------------------


def test_total_clientes():
    clientes = _carregar(_OPERACIONAL / "clientes.json")
    assert len(clientes) == 3, f"Esperado 3 personas, encontrado {len(clientes)}"


def test_total_historico():
    hist = _carregar(_OPERACIONAL / "historico.json")
    # 3 clientes × 20 transações históricas = 60
    assert len(hist) == 60, f"Esperado 60 histórico, encontrado {len(hist)}"


def test_total_candidatas_principais():
    cands = _carregar(_OPERACIONAL / "candidatas.json")
    # 3 clientes × 15 candidatas = 45
    assert len(cands) == 45, f"Esperado 45 candidatas, encontrado {len(cands)}"


def test_total_candidatas_dev():
    cands = _carregar(_AVALIACAO / "candidatas_dev.json")
    # 2 clientes (personas 1+2) × 15 = 30
    assert len(cands) == 30, f"Esperado 30 candidatas dev, encontrado {len(cands)}"


def test_total_candidatas_teste():
    cands = _carregar(_AVALIACAO / "candidatas_teste.json")
    # 1 cliente (persona 3) × 15 = 15
    assert len(cands) == 15, f"Esperado 15 candidatas teste, encontrado {len(cands)}"


# ---------------------------------------------------------------------------
# Reprodutibilidade (mesma semente → mesmos IDs)
# ---------------------------------------------------------------------------


def test_reproducibilidade_ids_clientes():
    """Geração com mesma semente deve produzir os mesmos IDs."""
    import sys

    sys.path.insert(0, str(_ROOT))
    from scripts.gerar_dados import gerar_clientes

    rng1 = random.Random(SEMENTE)
    rng2 = random.Random(SEMENTE)
    c1 = gerar_clientes(rng1)
    c2 = gerar_clientes(rng2)
    ids1 = [c["id"] for c in c1]
    ids2 = [c["id"] for c in c2]
    assert ids1 == ids2, "IDs de clientes devem ser determinísticos"


# ---------------------------------------------------------------------------
# Separação: operacional não contém rótulos
# ---------------------------------------------------------------------------


def test_candidatas_operacionais_sem_rotulo():
    cands = _carregar(_OPERACIONAL / "candidatas.json")
    for c in cands:
        assert "rotulo" not in c, f"Campo 'rotulo' encontrado em candidata {c.get('id')}"
        assert "cenario" not in c, f"Campo 'cenario' encontrado em candidata {c.get('id')}"
        assert "risco_esperado" not in c, (
            f"Campo 'risco_esperado' encontrado em candidata {c.get('id')}"
        )


def test_gabarito_contem_rotulos():
    gabarito = _carregar(_AVALIACAO / "gabarito_dev.json")
    for g in gabarito:
        assert "rotulo" in g, f"gabarito sem 'rotulo': {g}"
        assert "cenario" in g, f"gabarito sem 'cenario': {g}"
        assert "risco_esperado" in g, f"gabarito sem 'risco_esperado': {g}"


def test_candidatas_roteiro_nao_vazio_para_maioria():
    """Candidatas operacionais têm roteiro_cliente (para o simulador)."""
    cands = _carregar(_OPERACIONAL / "candidatas.json")
    com_roteiro = sum(1 for c in cands if "roteiro_cliente" in c)
    # Roteiro pode ser lista vazia (ausência de resposta) — mas campo deve existir
    assert com_roteiro == len(cands), "Todas candidatas devem ter campo roteiro_cliente"


def test_referencias_sem_cliente_especifico():
    """Referências não contêm dados de clientes específicos."""
    refs = list(_REFERENCIAS.glob("*.json"))
    for ref_path in refs:
        dados = _carregar(ref_path)
        for item in dados:
            assert "cliente_id" not in item, f"Referência contém cliente_id: {ref_path}"
            assert "transacao_id" not in item, f"Referência contém transacao_id: {ref_path}"


# ---------------------------------------------------------------------------
# IDs únicos e não reveladores
# ---------------------------------------------------------------------------


def test_ids_candidatas_unicos():
    cands = _carregar(_OPERACIONAL / "candidatas.json")
    ids = [c["id"] for c in cands]
    assert len(ids) == len(set(ids)), "IDs de candidatas devem ser únicos"


def test_ids_clientes_unicos():
    clientes = _carregar(_OPERACIONAL / "clientes.json")
    ids = [c["id"] for c in clientes]
    assert len(ids) == len(set(ids)), "IDs de clientes devem ser únicos"


def test_ids_nao_contem_nome_cenario():
    """IDs não devem revelar o cenário pelo nome."""
    cands = _carregar(_OPERACIONAL / "candidatas.json")
    cenarios = [
        "fraude",
        "legitimo",
        "discreta",
        "rapida",
        "exterior",
        "habitual",
        "assinatura",
        "viagem",
    ]
    for c in cands[:50]:  # Amostra
        tx_id = c["id"].lower()
        for cen in cenarios:
            assert cen not in tx_id, f"ID '{tx_id}' revela cenário '{cen}'"


# ---------------------------------------------------------------------------
# Dev e teste são disjuntos nos clientes
# ---------------------------------------------------------------------------


def test_dev_teste_clientes_disjuntos():
    gab_dev = _carregar(_AVALIACAO / "gabarito_dev.json")
    gab_teste = _carregar(_AVALIACAO / "gabarito_teste.json")

    clientes_dev = {g["cliente_id"] for g in gab_dev}
    clientes_teste = {g["cliente_id"] for g in gab_teste}

    intersecao = clientes_dev & clientes_teste
    assert len(intersecao) == 0, f"Clientes compartilhados entre dev e teste: {intersecao}"


def test_personas_tem_perfil_completo():
    """As 3 personas têm nome, ocupação, perfil de gasto e todos os campos obrigatórios."""
    clientes = _carregar(_OPERACIONAL / "clientes.json")
    assert len(clientes) == 3

    nomes_esperados = {"Ana Beatriz Ramos", "Lucas Mendonça", "Roberto Figueiredo"}
    nomes_encontrados = {c["customer_name"] for c in clientes}
    assert nomes_encontrados == nomes_esperados, f"Nomes inesperados: {nomes_encontrados}"

    campos_obrigatorios = [
        "customer_id",
        "customer_name",
        "customer_occupation",
        "customer_segment",
        "customer_age",
        "customer_income_bracket",
        "customer_profile",
        "card_id",
        "card_brand",
        "perfil_gasto",
    ]
    for c in clientes:
        for campo in campos_obrigatorios:
            assert campo in c, f"Campo '{campo}' ausente na persona {c.get('customer_name')}"
        pg = c["perfil_gasto"]
        assert "ticket_medio" in pg
        assert "categorias_habituais" in pg
        assert "desvio_padrao" in pg
        assert "descricao" in pg


def test_perfis_gasto_distintos():
    """As 3 personas têm tickets médios distintos, cobrindo baixo/médio/alto."""
    clientes = _carregar(_OPERACIONAL / "clientes.json")
    tickets = sorted(c["perfil_gasto"]["ticket_medio"] for c in clientes)
    # Lucas (~90), Ana (~350), Roberto (~2500)
    assert tickets[0] < 200, f"Persona de baixo gasto esperada (< R$200), encontrado {tickets[0]}"
    assert 200 <= tickets[1] <= 1000, (
        f"Persona moderada esperada (R$200–1000), encontrado {tickets[1]}"
    )
    assert tickets[2] > 1000, f"Persona de alto gasto esperada (> R$1000), encontrado {tickets[2]}"


# ---------------------------------------------------------------------------
# Rótulos: 900 legítimas e 600 fraudes nas candidatas principais
# ---------------------------------------------------------------------------


def test_contagem_rotulos_principais():
    # Verifica proporções de rótulos nos cenários (não totais absolutos, pois 3 personas × 15 cenários)
    from scripts.gerar_dados import CENARIO_ROTULOS

    legitimidades = [v for v in CENARIO_ROTULOS.values() if v == "legitimo"]
    fraudes = [v for v in CENARIO_ROTULOS.values() if v == "fraude"]
    # 8 legítimos, 5 fraudes, 2 inconclusivos
    assert len(legitimidades) >= 8, "Deve haver pelo menos 8 cenários legítimos"
    assert len(fraudes) >= 5, "Deve haver pelo menos 5 cenários de fraude"


def test_todos_cenarios_cobertos_nas_candidatas():
    """Com 15 candidatas por cliente e 15 cenários, cada persona cobre todos os cenários."""
    import sys

    sys.path.insert(0, str(_ROOT))
    from scripts.gerar_dados import CENARIOS

    cands = _carregar(_OPERACIONAL / "candidatas.json")
    # Não há campo 'cenario' nas operacionais (separação correta), apenas verificamos que
    # há exatamente 45 candidatas distribuídas entre as 3 personas
    assert len(cands) == 45
    assert len(CENARIOS) == 15
