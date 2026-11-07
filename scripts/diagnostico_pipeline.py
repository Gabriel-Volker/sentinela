"""scripts/diagnostico_pipeline.py — Testa o pipeline completo (sem IBM)."""

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from fraude.contratos import parsear_analise, validar_evidencias
from fraude.coordenador import _corrigir_caminhos_evidencias, _normalizar_evidencias
from fraude.repositorio import buscar_transacao

# Resposta real do Frontier (TX-ENBSYWPG, copiada do diagnóstico)
dados = {
    "versao": 2,
    "transacao_id": "TX-ENBSYWPG",
    "risco": "medio",
    "acao": "validar",
    "evidencias": [
        "/divergencia_geografica: Cliente reside em Porto Alegre/RS, mas a transacao ocorre em Belem/PA.",
        "/categoria_atipica: Compra em eletronicos (Casas Bahia, MCC 5732), fora das categorias habituais.",
        "/valor_acima_ticket_medio: Valor de R$ 658,63 acima do ticket medio de R$ 350,00.",
        "/mitigante_canal_dispositivo: Transacao via app_mobile com dispositivo conhecido.",
        "/mitigante_perfil_cliente: Cliente com rating baixo risco, segmento prime.",
    ],
    "informacoes_ausentes": [],
    "justificativa": "Risco medio por divergencia geografica e categoria atipica.",
}

print("=== Passo 1: Normalizar evidencias ===")
dados["evidencias"] = _normalizar_evidencias(dados["evidencias"])
for ev in dados["evidencias"]:
    print(f"  {ev}")

print("\n=== Passo 2: parsear_analise ===")
try:
    analise = parsear_analise(dados)
    print(f"  OK: risco={analise.risco.value}, acao={analise.acao.value}")
except Exception as e:
    print(f"  ERRO: {e}")
    sys.exit(1)

print("\n=== Passo 3: buscar_transacao ===")
tx = buscar_transacao("TX-ENBSYWPG")
if tx is None:
    print("  ERRO: transacao nao encontrada")
    sys.exit(1)
print(f"  OK: {tx.get('id')}, valor={tx.get('valor')}")

print("\n=== Passo 4: corrigir_caminhos_evidencias ===")
evidencias_dicts = [e.model_dump() for e in analise.evidencias]
evidencias_corrigidas = _corrigir_caminhos_evidencias(evidencias_dicts, tx)
if not evidencias_corrigidas:
    print("  ERRO: nenhuma evidencia corrigida")
    sys.exit(1)
for ev in evidencias_corrigidas:
    print(f"  {ev['caminho']}: {ev['descricao'][:50]}")

print("\n=== Passo 5: reparsear com evidencias corrigidas ===")
try:
    dados_corrigidos = {**dados, "evidencias": evidencias_corrigidas}
    analise2 = parsear_analise(dados_corrigidos)
    print(f"  OK: {len(analise2.evidencias)} evidencias")
except Exception as e:
    print(f"  ERRO: {e}")
    sys.exit(1)

print("\n=== Passo 6: validar_evidencias ===")
try:
    ev_com_valor = validar_evidencias(analise2.evidencias, tx)
    for ev in ev_com_valor:
        print(f"  {ev.caminho} = {ev.valor!r}")
except Exception as e:
    print(f"  ERRO: {e}")
    sys.exit(1)

print("\n=== PIPELINE COMPLETO — SUCESSO ===")
