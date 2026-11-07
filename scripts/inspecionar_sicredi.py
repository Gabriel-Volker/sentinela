"""
scripts/inspecionar_sicredi.py — Lê as primeiras linhas do CSV da Sicredi
e extrai o mapeamento de colunas para o formato Sentinela.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_CSV = _ROOT / "Case - Prevenção a Fraudes - Sicredi" / "case_transacoes_cartoes_sintetico_900k.csv"


def main() -> None:
    with open(_CSV, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        rows = []
        for i, row in enumerate(reader):
            rows.append(row)
            if i >= 9:
                break

    print(f"Total colunas: {len(header)}")
    print()

    key_cols = [
        "customer_id",
        "customer_name",
        "customer_city",
        "customer_region",
        "customer_country_code",
        "customer_occupation",
        "customer_segment",
        "customer_risk_rating",
        "customer_registration_date",
        "card_id",
        "card_brand",
        "card_last4",
        "card_type",
        "card_status",
        "card_credit_limit",
        "card_expiration_date",
        "amount",
        "currency_code",
        "acceptor_name",
        "acceptor_city",
        "acceptor_country_code",
        "acceptor_region",
        "acceptor_latitude",
        "acceptor_longitude",
        "mcc",
        "event_occurred_at",
        "local_datetime",
        "card_presence_flag",
        "device_id",
        "device_fingerprint",
        "device_ip",
        "device_ip_country_code",
        "auth_type",
        "tipo_transacao",
        "feedback_label",
        "cp_card_prev_term_dist_1h",
        "cp_card_term_vel_1h",
        "cardid_count_1m",
    ]

    print("=== Colunas chave ===")
    for col in key_cols:
        if col in header:
            vals = [r[col] for r in rows[:5]]
            print(f"  {col}: {vals}")
        else:
            print(f"  {col}: NAO ENCONTRADO")

    # Fraudes nas primeiras linhas
    print("\n=== feedback_label nas primeiras 10 linhas ===")
    for r in rows:
        print(
            f"  {r.get('feedback_label', '?')} | {r.get('customer_name', '?')} | {r.get('amount', '?')} | {r.get('acceptor_name', '?')} | {r.get('event_occurred_at', '?')}"
        )

    # Colunas por categoria
    print("\n=== Colunas por categoria ===")
    cats = {}
    for col in header:
        if col.startswith("customer_"):
            cats.setdefault("cliente", []).append(col)
        elif col.startswith("card_"):
            cats.setdefault("cartao", []).append(col)
        elif col.startswith("acceptor_"):
            cats.setdefault("estabelecimento", []).append(col)
        elif col.startswith("device_"):
            cats.setdefault("dispositivo", []).append(col)
        elif col.startswith("account_"):
            cats.setdefault("conta", []).append(col)
        else:
            cats.setdefault("outro", []).append(col)
    for cat, cols in cats.items():
        print(f"  {cat}: {len(cols)} colunas")
        if cat != "outro":
            print(f"    {cols}")


if __name__ == "__main__":
    main()
