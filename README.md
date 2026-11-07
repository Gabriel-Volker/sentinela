# Sentinela

MVP universitário de análise de fraudes em transações de cartão, desenvolvido no contexto do **IBM SkillsBuild**.

> **Demonstração local.** Dados sintéticos. Operações bancárias simuladas.
> Reiniciar o servidor perde o estado da sessão atual.

---

## Pré-requisitos

| Item | Versão mínima |
|---|---|
| Python | 3.11 – 3.14 |
| pip | 23+ |

---

## Instalação

```bash
# 1. Clone o repositório
git clone https://github.com/Gabriel-Volker/sentinela.git
cd sentinela

# 2. Instale as dependências (ambiente virtual recomendado)
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -e ".[dev]"
```

---

## Configuração

```bash
# Copie o exemplo e preencha com suas credenciais
cp .env.example .env
```

Edite `.env`:

```
# NÃO termine WXO_SERVICE_URL com /v1 — o adaptador adiciona /v1/orchestrate/... automaticamente.
WXO_SERVICE_URL=https://sua-instancia.orchestrate.ibm.com
WXO_API_KEY=sua-chave-iam
WXO_AGENT_ANALISE_ID=uuid-do-agente-analise
WXO_AGENT_VALIDACAO_ID=uuid-do-agente-validacao
SENTINELA_HOST=127.0.0.1
SENTINELA_PORT=8080
```

Sem `.env` configurado, o servidor inicia em **modo offline** (sem chamadas IBM).

---

## Geração dos dados sintéticos

```bash
python scripts/gerar_dados.py
```

O catálogo operacional já está versionado. Não execute o gerador para conectar a IBM.
Ele substitui os arquivos e altera os IDs; após gerar dados novos, sincronize as três ferramentas.

Cria os arquivos em `dados/mock/operacional/` e `dados/mock/referencias/`.
O gabarito vai para `dados/mock/avaliacao/` (não versionado).

---

## Importação de ferramentas e agentes (requer credenciais)

```bash
# Importar as três ferramentas remotas
python scripts/importar_ferramentas.py

# Importar os dois agentes
python scripts/importar_agentes.py
```

Use `--dry-run` para simular sem alterar a instância.
Use `--force-update` para atualizar recursos existentes.

Após a importação, anote os IDs exibidos e adicione-os ao `.env`.

---

## Executar o servidor

```bash
python web/servidor.py
```

Acesse: **http://127.0.0.1:8080**

Para mudar a porta:

```bash
SENTINELA_PORT=9090 python web/servidor.py
```

---

## Testes

```bash
# Suíte local, sem chamadas IBM
python -m pytest
node --test tests/frontend.test.cjs

# Testes rápidos (sem HTTP)
python -m pytest tests/test_contratos.py tests/test_dados.py tests/test_servico.py

# Com cobertura
python -m pytest --cov=fraude --cov=ibm --cov=web
```

---

## Lint

```bash
python -m ruff check fraude/ ibm/ web/rotas.py web/servidor.py ferramentas/ scripts/
python -m ruff format fraude/ ibm/ web/rotas.py web/servidor.py ferramentas/ scripts/
```

---

## Estrutura do projeto

```
fraude/          Contratos, estados, serviço e coordenador
ibm/             Autenticação IAM e adaptador Orchestrate
ferramentas/     Três ferramentas remotas (@tool)
agentes/         Definições YAML dos dois agentes
web/             Servidor HTTP e frontend estático
scripts/         Gerador de dados e scripts de importação
dados/mock/      Dados sintéticos (operacional + avaliacao)
tests/           Testes determinísticos
docs/            Documentação complementar
```

---

## Fluxo de análise de risco médio

Para transações de risco **médio**, o servidor retorna o parecer inicial imediatamente
após a análise e inicia o agente de validação em segundo plano. O frontend faz poll
via `POST /casos/{id}/consultar` para obter a primeira pergunta ao cliente quando
o agente de validação concluir. Isso elimina a espera de 2× execução na resposta HTTP.

---

## Limitações e pendências

- Integração real validada no fluxo de risco médio em 06/11/2026. Consulte `docs/VALIDACAO.md`.
- Persistência: estado de sessão em memória — reiniciar perde os casos.
- Exportações não restauram sessões automaticamente.
- Hospedagem remota não está provisionada (demonstração local).
- Caminhos de evidência nos YAMLs devem usar `/valor`, `/canal`, `/estabelecimento/nome`, etc.
  (sem prefixo `/transacao/`). Veja `AGENTS.md` para a lista de campos disponíveis.

Veja [`PROJETO.md`](PROJETO.md) para o escopo completo e [`docs/ibm_skillsbuild.md`](docs/ibm_skillsbuild.md) para o guia de conexão IBM.


## Sincronizar a instância existente

Configure os dois IDs de agentes e os três IDs de ferramentas no `.env`.
O comando abaixo compara nomes, modelo, instruções, contexto, vínculos, código e dados:

```bash
python scripts/sincronizar_orchestrate.py
```

Uma divergência retorna código de saída 1. Para atualizar os mesmos recursos:

```bash
python scripts/sincronizar_orchestrate.py --aplicar
```

Esse comando altera a IBM. Use-o com autorização da equipe. Ele salva backups em
`.local/orchestrate/`, envia os pacotes e verifica os arquivos por novo download.
Não cria instâncias nem regenera o catálogo. Os YAMLs mantêm o modelo Sonnet 4.6
que já estava configurado na instância.

O teste real fica separado da suíte rápida e consome chamadas IBM:

```bash
python scripts/verificar_fluxo_orchestrate.py --transacao TX-L9YKVZ1B --resposta "Sim, reconheço a compra."
```

Use uma compra sintética para a qual o agente solicite confirmação. O teste falha
se não exercitar a conversa ou se faltar um resultado verificável. Veja
`docs/VALIDACAO.md` para os resultados observados e os limites da validação.
