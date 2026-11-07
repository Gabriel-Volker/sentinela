"""
scripts/gerar_dados.py — Gerador reproduzível de dados sintéticos.

Formato alinhado ao vocabulário da Sicredi (campos customer_id, card_id,
amount, acceptor_name, mcc, event_occurred_at, card_presence_flag, device_id,
cp_card_prev_term_dist_1h, cp_card_term_vel_1h, feedback_label, etc.).

Semente: 20260930
Personas (3 clientes fixos com perfis realistas):
  - Ana Beatriz Ramos   — gastos moderados  (ticket médio ~R$ 350)
  - Lucas Mendonça      — gastos baixos     (ticket médio ~R$  90)
  - Roberto Figueiredo  — gastos elevados   (ticket médio ~R$ 2.500)

Totais:
  - 3 clientes fictícios (personas fixas)
  - 45 compras candidatas (15 por cliente, cobrindo todos os 15 cenários)
  - 60 compras históricas (20 por cliente)
  - 30 candidatas de desenvolvimento (clientes 1 e 2)
  - 15 candidatas de teste (cliente 3, distinto)
  - 15 cenários cobertos pelas candidatas
  - 15 referências (somente do desenvolvimento)

Separa fisicamente:
  operacional/   — dados disponíveis para análise (sem rótulos/gabarito)
  referencias/   — exemplos de desenvolvimento (sem cliente atual)
  avaliacao/     — gabarito + divisão de conjuntos (não servido pelo frontend)

IDs, posições e nomes não revelam cenário ou rótulo.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SEMENTE = 20260930
PROJETO_ROOT = Path(__file__).parent.parent
OPERACIONAL = PROJETO_ROOT / "dados" / "mock" / "operacional"
REFERENCIAS = PROJETO_ROOT / "dados" / "mock" / "referencias"
AVALIACAO = PROJETO_ROOT / "dados" / "mock" / "avaliacao"

# ---------------------------------------------------------------------------
# Parâmetros de geração
# ---------------------------------------------------------------------------

N_CLIENTES = 3
N_CANDIDATAS_POR_CLIENTE = 15  # 45 total — um por cenário por cliente
N_HISTORICO_POR_CLIENTE = 20  # 60 total
N_CLIENTES_DEV = 2  # personas 1 e 2
N_CLIENTES_TESTE = 1  # persona 3
N_CANDIDATAS_DEV = N_CLIENTES_DEV * N_CANDIDATAS_POR_CLIENTE  # 30
N_CANDIDATAS_TESTE = N_CLIENTES_TESTE * N_CANDIDATAS_POR_CLIENTE  # 15
N_CENARIOS = 15
N_CANDIDATAS_POR_CENARIO = N_CLIENTES  # 3 candidatas por cenário (uma por persona)
N_REFERENCIAS = 15

# ---------------------------------------------------------------------------
# Cenários definidos
# ---------------------------------------------------------------------------

CENARIOS = [
    "compra_habitual",
    "compra_online_conhecida",
    "assinatura",
    "novo_estabelecimento",
    "viagem_legitima",
    "valor_elevado_legitimo",
    "dispositivo_novo_legitimo",
    "compra_noturna_legitima",
    "resposta_inconclusiva",
    "ausencia_resposta",
    "compra_nao_reconhecida",
    "fraude_discreta",
    "sequencia_rapida_suspeita",
    "exterior_dispositivo_novo",
    "valor_muito_elevado_dispositivo_novo",
]

CENARIO_ROTULOS: dict[str, str] = {
    "compra_habitual": "legitimo",
    "compra_online_conhecida": "legitimo",
    "assinatura": "legitimo",
    "novo_estabelecimento": "legitimo",
    "viagem_legitima": "legitimo",
    "valor_elevado_legitimo": "legitimo",
    "dispositivo_novo_legitimo": "legitimo",
    "compra_noturna_legitima": "legitimo",
    "resposta_inconclusiva": "legitimo",
    "ausencia_resposta": "inconclusivo",
    "compra_nao_reconhecida": "fraude",
    "fraude_discreta": "fraude",
    "sequencia_rapida_suspeita": "fraude",
    "exterior_dispositivo_novo": "fraude",
    "valor_muito_elevado_dispositivo_novo": "fraude",
}

CENARIO_RISCO_ESPERADO: dict[str, str] = {
    "compra_habitual": "baixo",
    "compra_online_conhecida": "baixo",
    "assinatura": "baixo",
    "novo_estabelecimento": "medio",
    "viagem_legitima": "medio",
    "valor_elevado_legitimo": "medio",
    "dispositivo_novo_legitimo": "medio",
    "compra_noturna_legitima": "baixo",
    "resposta_inconclusiva": "medio",
    "ausencia_resposta": "medio",
    "compra_nao_reconhecida": "alto",
    "fraude_discreta": "medio",
    "sequencia_rapida_suspeita": "alto",
    "exterior_dispositivo_novo": "alto",
    "valor_muito_elevado_dispositivo_novo": "alto",
}

# ---------------------------------------------------------------------------
# Dados de referência — vocabulário alinhado à Sicredi
# ---------------------------------------------------------------------------

# MCC codes reais por categoria
MCC_POR_CATEGORIA: dict[str, str] = {
    "supermercado": "5411",
    "farmacia": "5912",
    "posto_combustivel": "5541",
    "restaurante": "5812",
    "streaming": "7994",
    "assinatura_software": "7372",
    "vestuario": "5651",
    "eletronicos": "5732",
    "viagem": "4511",
    "hotel": "7011",
    "transporte": "4121",
    "educacao": "8299",
    "saude": "8011",
    "lazer": "7922",
}

CATEGORIAS = list(MCC_POR_CATEGORIA.keys())

CANAIS = ["presencial", "online", "app_mobile", "telefone"]
TIPOS_TRANSACAO = ["credito", "debito"]

PAISES_BR = ["BR"]
PAISES_EXT = ["US", "FR", "DE", "AR", "GB", "MX", "JP", "AU"]

# Cidades e estados BR (formato Sicredi)
CIDADES_BR: list[tuple[str, str]] = [
    ("São Paulo", "SP"),
    ("Rio de Janeiro", "RJ"),
    ("Curitiba", "PR"),
    ("Porto Alegre", "RS"),
    ("Belo Horizonte", "MG"),
    ("Brasília", "DF"),
    ("Fortaleza", "CE"),
    ("Salvador", "BA"),
    ("Manaus", "AM"),
    ("Recife", "PE"),
    ("Goiânia", "GO"),
    ("Florianópolis", "SC"),
    ("Campinas", "SP"),
    ("Porto Velho", "RO"),
    ("Belém", "PA"),
]

CIDADES_EXT: list[tuple[str, str]] = [
    ("New York", "US"),
    ("Paris", "FR"),
    ("Berlin", "DE"),
    ("Buenos Aires", "AR"),
    ("London", "GB"),
    ("Mexico City", "MX"),
    ("Tokyo", "JP"),
    ("Sydney", "AU"),
]

NOMES_MASCULINOS = [
    "Carlos",
    "João",
    "Pedro",
    "Lucas",
    "Rafael",
    "Rodrigo",
    "Felipe",
    "Gustavo",
    "Eduardo",
    "Anderson",
    "Thiago",
    "Bruno",
    "Fernando",
    "Leonardo",
    "Marcelo",
    "André",
    "Diego",
    "Vinicius",
    "Gabriel",
    "Matheus",
    "Paulo",
    "Ricardo",
    "Henrique",
    "Jorge",
]

NOMES_FEMININOS = [
    "Ana",
    "Maria",
    "Juliana",
    "Fernanda",
    "Camila",
    "Amanda",
    "Larissa",
    "Beatriz",
    "Vanessa",
    "Luciana",
    "Patrícia",
    "Renata",
    "Mariana",
    "Carla",
    "Daniela",
    "Sandra",
    "Cristina",
    "Aline",
    "Tatiana",
    "Bruna",
    "Letícia",
    "Gabriela",
    "Isabela",
    "Natália",
]

SOBRENOMES = [
    "Silva",
    "Santos",
    "Oliveira",
    "Souza",
    "Lima",
    "Pereira",
    "Costa",
    "Ferreira",
    "Rodrigues",
    "Almeida",
    "Nascimento",
    "Araujo",
    "Carvalho",
    "Gomes",
    "Martins",
    "Ribeiro",
    "Barbosa",
    "Cardoso",
    "Melo",
    "Teixeira",
    "Fernandes",
    "Castro",
    "Moreira",
    "Azevedo",
]

OCUPACOES = [
    "analista",
    "engenheiro",
    "médico",
    "professor",
    "advogado",
    "comerciante",
    "servidor_publico",
    "aposentado",
    "estudante",
    "autonomo",
]

SEGMENTOS = ["varejo", "prime", "corporate", "pj", "universitario"]

BANDEIRAS = ["Visa", "Mastercard", "Elo", "Amex"]

DISPOSITIVOS: list[tuple[str, str]] = [
    ("iPhone 15", "apple_ios"),
    ("Samsung Galaxy S24", "android"),
    ("Google Pixel 8", "android"),
    ("Motorola Edge 50", "android"),
    ("iPad Pro", "apple_ipados"),
    ("MacBook Air", "macos"),
    ("notebook_windows", "windows"),
    ("desktop_windows", "windows"),
    ("Xiaomi Redmi Note 13", "android"),
]

REDES_ESTABELECIMENTOS: dict[str, list[str]] = {
    "supermercado": ["Carrefour", "Pão de Açúcar", "Extra", "Atacadão", "Assaí"],
    "farmacia": ["Raia", "Drogasil", "Pacheco", "Ultrafarma", "Panvel"],
    "posto_combustivel": ["Ipiranga", "Shell", "BP", "Petrobras", "Raízen"],
    "restaurante": ["McDonald's", "Burger King", "Outback", "Subway", "iFood"],
    "streaming": ["Netflix", "Spotify", "Disney+", "Max", "Amazon Prime"],
    "assinatura_software": ["Adobe CC", "Microsoft 365", "GitHub", "Notion", "Slack"],
    "vestuario": ["Renner", "C&A", "Riachuelo", "Zara", "H&M"],
    "eletronicos": ["Magazine Luiza", "Americanas", "Casas Bahia", "Fast Shop"],
    "viagem": ["LATAM", "GOL", "Azul", "Booking.com", "Airbnb"],
    "hotel": ["Ibis", "Marriott", "Holiday Inn", "Novotel", "Windsor"],
    "transporte": ["Uber", "99", "Cabify", "BlaBlaCar"],
    "educacao": ["Udemy", "Coursera", "Duolingo Plus", "Alura", "FIAP"],
    "saude": ["Unimed", "Amil", "Bradesco Saúde", "SulAmérica"],
    "lazer": ["Ingresso.com", "Sympla", "Ticketmaster", "Steam"],
}

# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------


def _id_tx(rng: random.Random) -> str:
    sufixo = "".join(rng.choices("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789", k=8))
    return f"TX-{sufixo}"


def _id_cliente(idx: int) -> str:
    """ID no formato Sicredi: CUST_SYN_XXXXXXX"""
    return f"CUST_SYN_{idx:07d}"


def _id_cartao(idx: int) -> str:
    return f"CARD_SYN_{idx:07d}"


def _id_conta(idx: int) -> str:
    return f"ACC_SYN_{idx:07d}"


def _id_dispositivo(rng: random.Random) -> str:
    num = rng.randint(1, 200000)
    return f"DEV_SYN_{num:07d}"


def _id_fingerprint(rng: random.Random) -> str:
    num = rng.randint(1, 300000)
    return f"FP_SYN_{num:07d}"


def _id_acceptor(idx: int) -> str:
    return f"ESTABELECIMENTO_SINTETICO_{idx:05d}"


def _data_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _nome_pessoa(rng: random.Random) -> tuple[str, str, str]:
    genero = rng.choice(["M", "F"])
    primeiro = rng.choice(NOMES_MASCULINOS if genero == "M" else NOMES_FEMININOS)
    sobrenome = rng.choice(SOBRENOMES) + " " + rng.choice(SOBRENOMES)
    return primeiro, sobrenome, genero


# ---------------------------------------------------------------------------
# Personas fixas (3 clientes com perfis realistas)
# ---------------------------------------------------------------------------

#: Personas definidas estaticamente — determinísticas por construção.
#: Não usam o rng, pois os valores de perfil são fixos.
PERSONAS: list[dict] = [
    # ---- Persona 1: Ana Beatriz Ramos — gastos moderados -------------------
    {
        "customer_id": "CUST_SYN_0000001",
        "customer_name": "Ana Beatriz Ramos",
        "customer_city": "Porto Alegre",
        "customer_region": "RS",
        "customer_country_code": "BR",
        "customer_occupation": "farmacêutica",
        "customer_segment": "prime",
        "customer_risk_rating": "baixo",
        "customer_registration_date": "2019-03-15T00:00:00+00:00",
        "customer_gender": "F",
        "customer_age": 34,
        "customer_income_bracket": "media",
        "customer_profile": (
            "Farmacêutica, 34 anos, mora em Porto Alegre. "
            "Gasta principalmente em supermercados, farmácias e restaurantes. "
            "Faz compras online com frequência moderada. "
            "Viaja a lazer uma vez por ano. "
            "Ticket médio de R$ 350 por compra."
        ),
        "customer_total_credit_limit": 12000.00,
        "customer_total_balance": 4200.00,
        # Cartão principal
        "card_id": "CARD_SYN_0000001",
        "card_brand": "Mastercard",
        "card_last4": "4821",
        "card_type": "credito",
        "card_status": "ativo",
        "card_credit_limit": 12000.00,
        "card_expiration_date": "2028-06-01T00:00:00+00:00",
        # Conta
        "account_id": "ACC_SYN_0000001",
        "account_type": "corrente",
        "account_status": "ativa",
        "account_credit_limit": 12000.00,
        "account_balance": 3800.00,
        # Aliases Sentinela
        "id": "CUST_SYN_0000001",
        "nome": "Ana Beatriz Ramos",
        "cidade": "Porto Alegre",
        "estado": "RS",
        "pais": "BR",
        "perfil_gasto": {
            "ticket_medio": 350.0,
            "desvio_padrao": 80.0,
            "frequencia_mensal": 14,
            "limite_habitual_superior": 800.0,
            "categorias_habituais": [
                "supermercado",
                "farmacia",
                "restaurante",
                "streaming",
                "transporte",
            ],
            "canais_habituais": ["presencial", "online", "app_mobile"],
            "descricao": (
                "Gastos moderados e regulares. Concentra compras em Porto Alegre. "
                "Usa cartão de crédito para supermercado, farmácias e serviços digitais. "
                "Viagens ocasionais ao Sudeste."
            ),
        },
    },
    # ---- Persona 2: Lucas Mendonça — gastos baixos -------------------------
    {
        "customer_id": "CUST_SYN_0000002",
        "customer_name": "Lucas Mendonça",
        "customer_city": "Belo Horizonte",
        "customer_region": "MG",
        "customer_country_code": "BR",
        "customer_occupation": "estudante",
        "customer_segment": "universitario",
        "customer_risk_rating": "baixo",
        "customer_registration_date": "2022-08-20T00:00:00+00:00",
        "customer_gender": "M",
        "customer_age": 22,
        "customer_income_bracket": "baixa",
        "customer_profile": (
            "Estudante universitário, 22 anos, mora em Belo Horizonte. "
            "Renda limitada, gasta principalmente em alimentação rápida, "
            "transporte (apps) e assinaturas de streaming. "
            "Prefere canais online e app mobile. "
            "Ticket médio de R$ 90 por compra."
        ),
        "customer_total_credit_limit": 2500.00,
        "customer_total_balance": 1100.00,
        # Cartão principal
        "card_id": "CARD_SYN_0000002",
        "card_brand": "Visa",
        "card_last4": "7364",
        "card_type": "credito",
        "card_status": "ativo",
        "card_credit_limit": 2500.00,
        "card_expiration_date": "2027-12-01T00:00:00+00:00",
        # Conta
        "account_id": "ACC_SYN_0000002",
        "account_type": "corrente",
        "account_status": "ativa",
        "account_credit_limit": 2500.00,
        "account_balance": 420.00,
        # Aliases Sentinela
        "id": "CUST_SYN_0000002",
        "nome": "Lucas Mendonça",
        "cidade": "Belo Horizonte",
        "estado": "MG",
        "pais": "BR",
        "perfil_gasto": {
            "ticket_medio": 90.0,
            "desvio_padrao": 30.0,
            "frequencia_mensal": 18,
            "limite_habitual_superior": 200.0,
            "categorias_habituais": [
                "restaurante",
                "transporte",
                "streaming",
                "assinatura_software",
                "lazer",
            ],
            "canais_habituais": ["online", "app_mobile"],
            "descricao": (
                "Gastos baixos e frequentes. Uso intenso de apps de entrega e transporte. "
                "Assinaturas digitais e alimentação rápida dominam o histórico. "
                "Raramente compra presencialmente fora de BH."
            ),
        },
    },
    # ---- Persona 3: Roberto Figueiredo — gastos elevados -------------------
    {
        "customer_id": "CUST_SYN_0000003",
        "customer_name": "Roberto Figueiredo",
        "customer_city": "São Paulo",
        "customer_region": "SP",
        "customer_country_code": "BR",
        "customer_occupation": "empresário",
        "customer_segment": "corporate",
        "customer_risk_rating": "baixo",
        "customer_registration_date": "2015-11-05T00:00:00+00:00",
        "customer_gender": "M",
        "customer_age": 47,
        "customer_income_bracket": "alta",
        "customer_profile": (
            "Empresário, 47 anos, mora em São Paulo. "
            "Perfil de alto gasto: viagens internacionais frequentes, hospedagem "
            "em hotéis premium, eletrônicos e refeições em restaurantes finos. "
            "Usa Amex Platinum e possui limite elevado. "
            "Ticket médio de R$ 2.500 por compra."
        ),
        "customer_total_credit_limit": 85000.00,
        "customer_total_balance": 31000.00,
        # Cartão principal
        "card_id": "CARD_SYN_0000003",
        "card_brand": "Amex",
        "card_last4": "1093",
        "card_type": "credito",
        "card_status": "ativo",
        "card_credit_limit": 85000.00,
        "card_expiration_date": "2029-03-01T00:00:00+00:00",
        # Conta
        "account_id": "ACC_SYN_0000003",
        "account_type": "corrente",
        "account_status": "ativa",
        "account_credit_limit": 85000.00,
        "account_balance": 28500.00,
        # Aliases Sentinela
        "id": "CUST_SYN_0000003",
        "nome": "Roberto Figueiredo",
        "cidade": "São Paulo",
        "estado": "SP",
        "pais": "BR",
        "perfil_gasto": {
            "ticket_medio": 2500.0,
            "desvio_padrao": 800.0,
            "frequencia_mensal": 22,
            "limite_habitual_superior": 15000.0,
            "categorias_habituais": [
                "viagem",
                "hotel",
                "eletronicos",
                "restaurante",
                "saude",
            ],
            "canais_habituais": ["presencial", "online"],
            "descricao": (
                "Gastos elevados e variados. Viagens internacionais mensais. "
                "Compras de alto valor em eletrônicos e hotelaria. "
                "Histórico com muitas cidades brasileiras e internacionais."
            ),
        },
    },
]


def gerar_clientes(rng: random.Random) -> list[dict]:  # noqa: ARG001
    """Retorna as 3 personas fixas. O rng é aceito para manter a assinatura compatível."""
    return list(PERSONAS)


# ---------------------------------------------------------------------------
# Geração de transação (formato Sicredi)
# ---------------------------------------------------------------------------


def _gerar_transacao_base(
    rng: random.Random,
    tx_id: str,
    cliente: dict,
    data_base: datetime,
    cenario: str,
    dispositivo_pool: dict[str, str],
) -> dict:
    """Gera os campos de uma transação no formato Sicredi."""
    categoria = rng.choice(CATEGORIAS)
    redes = REDES_ESTABELECIMENTOS.get(categoria, ["Loja Genérica"])
    rede = rng.choice(redes)
    mcc = MCC_POR_CATEGORIA[categoria]
    tipo_transacao = rng.choice(TIPOS_TRANSACAO)
    card_presence = "N"  # default: cartão não presente

    # Parâmetros por cenário
    if cenario in ("compra_habitual", "assinatura", "compra_noturna_legitima"):
        valor = round(rng.uniform(20, 300), 2)
        cidade_acc, pais_acc = rng.choice(CIDADES_BR)
        dispositivo_novo = False
        card_presence = rng.choice(["Y", "N"])

    elif cenario == "compra_online_conhecida":
        valor = round(rng.uniform(15, 200), 2)
        cidade_acc, pais_acc = rng.choice(CIDADES_BR)
        dispositivo_novo = False
        card_presence = "N"
        categoria = rng.choice(["streaming", "assinatura_software"])
        redes = REDES_ESTABELECIMENTOS[categoria]
        rede = rng.choice(redes)
        mcc = MCC_POR_CATEGORIA[categoria]

    elif cenario in ("valor_elevado_legitimo",):
        valor = round(rng.uniform(2000, 15000), 2)
        cidade_acc, pais_acc = rng.choice(CIDADES_BR)
        dispositivo_novo = False
        card_presence = rng.choice(["Y", "N"])

    elif cenario == "valor_muito_elevado_dispositivo_novo":
        valor = round(rng.uniform(5000, 20000), 2)
        cidade_acc, pais_acc = rng.choice(CIDADES_BR)
        dispositivo_novo = True
        card_presence = "N"

    elif cenario in ("exterior_dispositivo_novo",):
        valor = round(rng.uniform(200, 3000), 2)
        cidade_acc, pais_acc = rng.choice(CIDADES_EXT)
        dispositivo_novo = True
        card_presence = "N"

    elif cenario == "viagem_legitima":
        valor = round(rng.uniform(200, 3000), 2)
        cidade_acc, pais_acc = rng.choice(CIDADES_EXT)
        dispositivo_novo = False
        card_presence = rng.choice(["Y", "N"])

    elif cenario == "fraude_discreta":
        valor = round(rng.uniform(50, 500), 2)
        cidade_acc, pais_acc = rng.choice(CIDADES_BR)
        dispositivo_novo = True
        card_presence = "N"

    elif cenario == "sequencia_rapida_suspeita":
        valor = round(rng.uniform(100, 1000), 2)
        cidade_acc, pais_acc = rng.choice(CIDADES_BR)
        dispositivo_novo = False
        card_presence = "N"

    else:
        valor = round(rng.uniform(50, 1500), 2)
        cidade_acc, pais_acc = rng.choice(CIDADES_BR)
        dispositivo_novo = cenario in ("dispositivo_novo_legitimo",)
        card_presence = rng.choice(["Y", "N"])

    moeda = "USD" if pais_acc == "US" else ("EUR" if pais_acc in ("FR", "DE", "GB") else "BRL")

    # Hora
    if cenario == "compra_noturna_legitima":
        hora = data_base.replace(hour=rng.randint(22, 23), minute=rng.randint(0, 59))
    elif cenario == "sequencia_rapida_suspeita":
        hora = data_base
    else:
        hora = data_base.replace(hour=rng.randint(7, 22), minute=rng.randint(0, 59))

    # Dispositivo — reutiliza do pool se não é novo
    if dispositivo_novo or cliente["customer_id"] not in dispositivo_pool:
        dev_nome, dev_lang = rng.choice(DISPOSITIVOS)
        dev_id = _id_dispositivo(rng)
        fp_id = _id_fingerprint(rng)
        if not dispositivo_novo:
            dispositivo_pool[cliente["customer_id"]] = dev_id
    else:
        dev_id = dispositivo_pool[cliente["customer_id"]]
        dev_nome, dev_lang = rng.choice(DISPOSITIVOS)
        fp_id = _id_fingerprint(rng)

    # Velocidade sintética (inspirada em cp_card_term_vel_1h e cp_card_prev_term_dist_1h)
    if cenario == "sequencia_rapida_suspeita":
        cp_vel = rng.randint(4, 8)
        cp_dist = round(rng.uniform(0.5, 5.0), 2)
        cardid_count = rng.randint(4, 8)
    elif cenario in ("exterior_dispositivo_novo", "valor_muito_elevado_dispositivo_novo"):
        cp_vel = rng.randint(1, 3)
        cp_dist = round(rng.uniform(50, 500), 2)
        cardid_count = rng.randint(1, 2)
    else:
        cp_vel = rng.randint(1, 3)
        cp_dist = round(rng.uniform(0.5, 50), 2)
        cardid_count = rng.randint(1, 2)

    # Eventos recentes
    eventos_recentes: list[dict] = []
    if cenario == "sequencia_rapida_suspeita":
        for i in range(rng.randint(3, 6)):
            eventos_recentes.append(
                {
                    "tipo": "transacao_rapida",
                    "minutos_atras": (i + 1) * rng.randint(1, 5),
                    "valor": round(rng.uniform(50, 500), 2),
                    "acceptor_city": rng.choice(CIDADES_BR)[0],
                }
            )
    elif cenario in (
        "exterior_dispositivo_novo",
        "dispositivo_novo_legitimo",
        "valor_muito_elevado_dispositivo_novo",
    ):
        eventos_recentes.append(
            {
                "tipo": "novo_dispositivo",
                "dias_atras": rng.randint(1, 7),
                "device_id": dev_id,
            }
        )
    elif cenario == "viagem_legitima":
        eventos_recentes.append(
            {
                "tipo": "viagem_registrada",
                "dias_atras": rng.randint(0, 3),
                "destino": pais_acc,
            }
        )

    # Roteiro do cliente
    roteiro = _gerar_roteiro(rng, cenario)

    # Número de acceptor baseado no nome (estável por rede)
    acceptor_num = abs(hash(rede)) % 3000 + 1

    return {
        # ---- Identificação (formato Sicredi) ----
        "id": tx_id,  # ID interno Sentinela
        "event_external_id": tx_id,  # alias Sicredi
        "event_occurred_at": _data_iso(hora),
        "local_datetime": _data_iso(hora),
        # ---- Cliente ----
        "customer_id": cliente["customer_id"],
        "customer_name": cliente["customer_name"],
        "customer_city": cliente["customer_city"],
        "customer_region": cliente["customer_region"],
        "customer_country_code": cliente["customer_country_code"],
        "customer_risk_rating": cliente["customer_risk_rating"],
        "customer_segment": cliente["customer_segment"],
        # ---- Cartão ----
        "card_id": cliente["card_id"],
        "card_brand": cliente["card_brand"],
        "card_last4": cliente["card_last4"],
        "card_type": cliente["card_type"],
        "card_status": "ativo",
        "card_credit_limit": cliente["card_credit_limit"],
        "card_presence_flag": card_presence,
        "tipo_transacao": tipo_transacao,
        # ---- Valor ----
        "amount": valor,
        "currency_code": moeda,
        "amount_fees": round(valor * rng.uniform(0, 0.02), 2),
        # ---- Estabelecimento ----
        "acceptor_id": f"ESTABELECIMENTO_SINTETICO_{acceptor_num:05d}",
        "acceptor_name": rede,
        "acceptor_city": cidade_acc,
        "acceptor_region": (pais_acc if pais_acc != "BR" else rng.choice(CIDADES_BR)[1]),
        "acceptor_country_code": "BR" if pais_acc not in PAISES_EXT else pais_acc,
        "mcc": mcc,
        # ---- Dispositivo ----
        "device_id": dev_id,
        "device_fingerprint": fp_id,
        "device_ip": f"{rng.randint(1, 254)}.{rng.randint(1, 254)}.{rng.randint(1, 254)}.{rng.randint(1, 254)}",
        "device_ip_country_code": "BR" if pais_acc not in PAISES_EXT else pais_acc,
        "device_language": dev_lang,
        # ---- Velocidade (inspirado em Sicredi) ----
        "cp_card_prev_term_dist_1h": cp_dist,
        "cp_card_term_vel_1h": cp_vel,
        "cardid_count_1m": cardid_count,
        # ---- Campos Sentinela internos (mantidos para compatibilidade) ----
        "cliente_id": cliente["customer_id"],  # alias Sentinela
        "data_hora": _data_iso(hora),  # alias Sentinela
        "valor": valor,  # alias Sentinela
        "moeda": moeda,
        "estabelecimento": {
            "nome": rede,
            "categoria": categoria,
        },
        "localizacao": {
            "cidade": cidade_acc,
            "pais": pais_acc,
        },
        "canal": ("online" if card_presence == "N" else rng.choice(["presencial", "app_mobile"])),
        "dispositivo": {
            "nome": dev_nome,
            "id": dev_id,
            "novo": dispositivo_novo,
        },
        "cartao": {
            "id": cliente["card_id"],
            "final": cliente["card_last4"],
            "tipo": cliente["card_type"],
            "bandeira": cliente["card_brand"],
        },
        "eventos_recentes": eventos_recentes,
        "roteiro_cliente": roteiro,
    }


def _gerar_roteiro(rng: random.Random, cenario: str) -> list[str]:
    roteiros: dict[str, list[list[str]]] = {
        "compra_nao_reconhecida": [
            ["Não, não fui eu.", "Alguém usou meu cartão sem autorização."],
            ["Com certeza não reconheço essa compra.", "Nunca estive nesse lugar."],
        ],
        "compra_habitual": [
            ["Sim, fiz essa compra hoje.", "Estava no supermercado."],
        ],
        "resposta_inconclusiva": [
            ["Não sei... pode ter sido.", "Estou em dúvida."],
            ["Talvez. Não lembro muito bem."],
        ],
        "ausencia_resposta": [
            [],
        ],
        "fraude_discreta": [
            ["Não, não reconheço.", "Nunca ouvi falar desse lugar."],
        ],
        "sequencia_rapida_suspeita": [
            ["Não fiz essas compras todas.", "Meu cartão foi clonado."],
        ],
        "exterior_dispositivo_novo": [
            ["Não estou fora do país.", "Meu cartão foi roubado."],
        ],
        "valor_muito_elevado_dispositivo_novo": [
            ["Não fiz essa compra.", "Esse valor é impossível."],
        ],
        "viagem_legitima": [
            ["Sim, estou viajando.", "Comprei no hotel."],
        ],
        "valor_elevado_legitimo": [
            ["Sim, comprei um notebook.", "Estava planejando há meses."],
        ],
        "novo_estabelecimento": [
            ["Sim, fui nessa loja pela primeira vez.", "Gostei bastante."],
        ],
        "dispositivo_novo_legitimo": [
            ["Troquei de celular há três dias.", "Era meu novo iPhone."],
        ],
        "compra_noturna_legitima": [
            ["Sim, estava no plantão e precisei comprar.", "Foi no posto."],
        ],
        "compra_online_conhecida": [
            ["Sim, renovei minha assinatura.", "É o Netflix."],
        ],
        "assinatura": [
            ["Sim, é o débito automático.", "É todo mês assim."],
        ],
    }
    opcoes = roteiros.get(cenario, [["Sim, reconheço."]])
    return rng.choice(opcoes)


# ---------------------------------------------------------------------------
# Geração de histórico (formato Sicredi)
# ---------------------------------------------------------------------------


def gerar_historico(rng: random.Random, clientes: list[dict]) -> list[dict]:
    historico = []
    data_base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    for cliente in clientes:
        cid = cliente["customer_id"]
        data = data_base - timedelta(days=rng.randint(30, 365))
        ticket_medio = cliente["perfil_gasto"]["ticket_medio"]
        desvio = cliente["perfil_gasto"].get("desvio_padrao", 50.0)
        for _j in range(N_HISTORICO_POR_CLIENTE):
            categoria = rng.choice(cliente["perfil_gasto"]["categorias_habituais"])
            redes = REDES_ESTABELECIMENTOS.get(categoria, ["Loja Genérica"])
            rede = rng.choice(redes)
            mcc = MCC_POR_CATEGORIA.get(categoria, "5999")
            valor = round(rng.gauss(ticket_medio, desvio), 2)
            valor = max(10.0, valor)
            data -= timedelta(days=rng.randint(1, 15))
            cidade_cli, estado_cli = cliente["customer_city"], cliente["customer_region"]
            historico.append(
                {
                    "id": _id_tx(rng),
                    "customer_id": cid,
                    "cliente_id": cid,
                    "event_occurred_at": _data_iso(data),
                    "data_hora": _data_iso(data),
                    "amount": valor,
                    "valor": valor,
                    "currency_code": "BRL",
                    "moeda": "BRL",
                    "acceptor_name": rede,
                    "acceptor_city": cidade_cli,
                    "acceptor_region": estado_cli,
                    "acceptor_country_code": "BR",
                    "mcc": mcc,
                    "estabelecimento": {
                        "nome": rede,
                        "categoria": categoria,
                    },
                    "categoria": categoria,
                    "cidade": cidade_cli,
                    "pais": "BR",
                    "canal": rng.choice(["presencial", "online"]),
                    "status": "aprovado",
                    "card_type": cliente["card_type"],
                    "card_presence_flag": rng.choice(["Y", "N"]),
                    "tipo_transacao": rng.choice(TIPOS_TRANSACAO),
                }
            )
    return historico


# ---------------------------------------------------------------------------
# Geração de candidatas
# ---------------------------------------------------------------------------


def gerar_candidatas(
    rng: random.Random,
    clientes: list[dict],
    n_por_cliente: int,
    data_base: datetime,
    ids_existentes: set[str],
    dispositivo_pool: dict[str, str],
) -> tuple[list[dict], list[dict]]:
    operacional = []
    gabarito = []

    cenario_idx = 0
    for cliente in clientes:
        data = data_base + timedelta(days=rng.randint(0, 30))
        for _j in range(n_por_cliente):
            cenario = CENARIOS[cenario_idx % N_CENARIOS]
            cenario_idx += 1

            while True:
                tx_id = _id_tx(rng)
                if tx_id not in ids_existentes:
                    ids_existentes.add(tx_id)
                    break

            tx_data = data + timedelta(hours=rng.randint(1, 72))
            tx = _gerar_transacao_base(rng, tx_id, cliente, tx_data, cenario, dispositivo_pool)

            roteiro = tx.pop("roteiro_cliente", [])

            operacional.append(tx)
            gabarito.append(
                {
                    "transacao_id": tx_id,
                    "customer_id": cliente["customer_id"],
                    "cliente_id": cliente["customer_id"],
                    "cenario": cenario,
                    "rotulo": CENARIO_ROTULOS[cenario],
                    "feedback_label": CENARIO_ROTULOS[cenario] == "fraude",
                    "risco_esperado": CENARIO_RISCO_ESPERADO[cenario],
                    "roteiro_cliente": roteiro,
                }
            )

    # Permuta e embaralha
    perm_idx = list(range(len(operacional)))
    rng.shuffle(perm_idx)
    operacional = [operacional[i] for i in perm_idx]
    gabarito_perm = [gabarito[i] for i in perm_idx]

    return operacional, gabarito_perm


# ---------------------------------------------------------------------------
# Referências
# ---------------------------------------------------------------------------


def gerar_referencias(gabarito_dev: list[dict]) -> list[dict]:
    por_cenario: dict[str, list[dict]] = {}
    for item in gabarito_dev:
        c = item["cenario"]
        por_cenario.setdefault(c, []).append(item)

    referencias = []
    cenarios_amostrados = list(por_cenario.keys())[:N_REFERENCIAS]
    for cenario in cenarios_amostrados:
        exemplos = por_cenario[cenario][:1]
        for ex in exemplos:
            referencias.append(
                {
                    "cenario_tipo": cenario,
                    "risco_esperado": ex["risco_esperado"],
                    "rotulo": ex["rotulo"],
                    "feedback_label": ex["feedback_label"],
                    "nota": (
                        "Exemplo de referência de desenvolvimento. "
                        "Não use como evidência do cliente atual. "
                        "Não apresente como prova desta análise."
                    ),
                }
            )
    return referencias


# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------


def main() -> None:
    print(f"Gerando dados com semente {SEMENTE}...")
    rng = random.Random(SEMENTE)

    for d in (OPERACIONAL, REFERENCIAS, AVALIACAO):
        d.mkdir(parents=True, exist_ok=True)

    # Pool de dispositivos por cliente (para reutilização estável)
    dispositivo_pool: dict[str, str] = {}

    # 1. Clientes
    clientes = gerar_clientes(rng)
    _salvar(OPERACIONAL / "clientes.json", clientes)
    print(f"  {len(clientes)} clientes gerados.")

    # 2. Histórico
    historico = gerar_historico(rng, clientes)
    _salvar(OPERACIONAL / "historico.json", historico)
    print(f"  {len(historico)} transações históricas geradas.")

    # 3. Candidatas principais
    data_base = datetime(2025, 11, 1, tzinfo=timezone.utc)
    ids_existentes: set[str] = set()
    candidatas_op, gabarito_principal = gerar_candidatas(
        rng, clientes, N_CANDIDATAS_POR_CLIENTE, data_base, ids_existentes, dispositivo_pool
    )

    # Injeta roteiro nas candidatas operacionais (acessível ao simulador)
    roteiro_por_id = {g["transacao_id"]: g["roteiro_cliente"] for g in gabarito_principal}
    for tx in candidatas_op:
        tx["roteiro_cliente"] = roteiro_por_id.get(tx["id"], [])

    _salvar(OPERACIONAL / "candidatas.json", candidatas_op)
    print(f"  {len(candidatas_op)} candidatas principais geradas.")

    # 4. Split dev/teste — determinístico: personas 1+2 → dev, persona 3 → teste
    clientes_dev = clientes[:N_CLIENTES_DEV]  # Ana + Lucas
    clientes_teste = clientes[N_CLIENTES_DEV:]  # Roberto

    data_dev = datetime(2026, 1, 1, tzinfo=timezone.utc)
    cands_dev_op, gabarito_dev = gerar_candidatas(
        rng, clientes_dev, N_CANDIDATAS_POR_CLIENTE, data_dev, ids_existentes, dispositivo_pool
    )
    for tx in cands_dev_op:
        rot = next(
            (g["roteiro_cliente"] for g in gabarito_dev if g["transacao_id"] == tx["id"]), []
        )
        tx["roteiro_cliente"] = rot

    _salvar(AVALIACAO / "candidatas_dev.json", cands_dev_op)
    _salvar(AVALIACAO / "gabarito_dev.json", gabarito_dev)
    print(f"  {len(cands_dev_op)} candidatas de desenvolvimento geradas.")

    data_teste = datetime(2026, 3, 1, tzinfo=timezone.utc)
    cands_teste_op, gabarito_teste = gerar_candidatas(
        rng, clientes_teste, N_CANDIDATAS_POR_CLIENTE, data_teste, ids_existentes, dispositivo_pool
    )
    for tx in cands_teste_op:
        rot = next(
            (g["roteiro_cliente"] for g in gabarito_teste if g["transacao_id"] == tx["id"]), []
        )
        tx["roteiro_cliente"] = rot

    _salvar(AVALIACAO / "candidatas_teste.json", cands_teste_op)
    _salvar(AVALIACAO / "gabarito_teste.json", gabarito_teste)
    print(f"  {len(cands_teste_op)} candidatas de teste geradas.")

    # 5. Candidatas por cenário — reutiliza o gabarito principal
    # Com 3 personas × 15 cenários, cada cenário aparece exatamente uma vez por persona.
    _salvar(AVALIACAO / "gabarito_cenarios.json", gabarito_principal)
    print(f"  {len(CENARIOS)} cenários cobertos nas candidatas principais.")

    # 6. Referências
    referencias = gerar_referencias(gabarito_dev)
    for j, ref in enumerate(referencias[:N_REFERENCIAS]):
        _salvar(REFERENCIAS / f"referencia_{j + 1:02d}.json", [ref])
    print(f"  {len(referencias[:N_REFERENCIAS])} referências de desenvolvimento geradas.")

    # 7. Resumo
    total_legitimo = sum(1 for g in gabarito_principal if g["rotulo"] == "legitimo")
    total_fraude = sum(1 for g in gabarito_principal if g["rotulo"] == "fraude")
    total_inconc = sum(1 for g in gabarito_principal if g["rotulo"] == "inconclusivo")
    print("\nResumo candidatas principais:")
    print(f"  Legítimas:     {total_legitimo}")
    print(f"  Fraudes:       {total_fraude}")
    print(f"  Inconclusivas: {total_inconc}")
    print(f"  Total:         {len(gabarito_principal)}")
    print("\nGeração concluída.")


def _salvar(caminho: Path, dados: Any) -> None:
    with caminho.open("w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        SEMENTE = int(sys.argv[1])
    main()
