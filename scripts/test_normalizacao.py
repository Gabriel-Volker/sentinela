"""scripts/test_normalizacao.py — Testa normalização de evidências."""

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from fraude.contratos import parsear_analise, validar_evidencias
from fraude.coordenador import _corrigir_caminhos_evidencias, _normalizar_evidencias
from fraude.repositorio import buscar_transacao

dados = {
    "versao": 2,
    "transacao_id": "TX-ENBSYWPG",
    "risco": "medio",
    "acao": "validar",
    "evidencias": [
        "/divergencia_geografica: Transacao realizada em Belem/PA, cliente reside em Porto Alegre/RS.",
        "/categoria_atipica: Compra em eletronicos (Casas Bahia, MCC 5732).",
        "/valor_acima_media: Valor R$ 658,63 acima do ticket medio historico.",
        "/mitigador_canal: Canal app_mobile e habitual da cliente.",
        "/mitigador_dispositivo: Google Pixel 8 (DEV_SYN_0059333) e dispositivo conhecido.",
    ],
    "informacoes_ausentes": [],
    "justificativa": "Risco medio pela combinacao de divergencia geografica e categoria atipica.",
}

# Passo 1: normaliza strings → objetos
dados["evidencias"] = _normalizar_evidencias(dados["evidencias"])
print("Apos normalizacao:")
for ev in dados["evidencias"]:
    print(f"  {ev}")

# Passo 2: parseia contrato
analise = parsear_analise(dados)
print(f"\nparsear_analise OK, risco: {analise.risco.value}")

# Passo 3: corrige caminhos inválidos
tx = buscar_transacao("TX-ENBSYWPG")
assert tx is not None
evidencias_corrigidas = _corrigir_caminhos_evidencias(
    [e.model_dump() for e in analise.evidencias], tx
)
print(f"Evidencias corrigidas: {len(evidencias_corrigidas)}")
for ev in evidencias_corrigidas:
    print(f"  {ev['caminho']}")

# Passo 4: reparseia e valida
if not evidencias_corrigidas:
    print("FALHOU: nenhuma evidencia corrigida")
    sys.exit(1)

dados2 = {**dados, "evidencias": evidencias_corrigidas}
analise2 = parsear_analise(dados2)
ev_com_valor = validar_evidencias(analise2.evidencias, tx)
print("\nvalidar_evidencias OK:")
for ev in ev_com_valor:
    print(f"  {ev.caminho} = {ev.valor}")

print("\nSUCESSO — pipeline completo funcionando")
