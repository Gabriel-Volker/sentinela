# Guia de Conexão IBM SkillsBuild

## Objetivo

Conectar o Sentinela à instância do watsonx Orchestrate da conta IBM Cloud da equipe.

---

## 1. Ingresso na conta IBM Cloud da equipe

1. Aguarde o convite por e-mail da IBM Cloud.
2. Clique no link do convite e faça login com sua IBM ID.
3. Após aceitar, acesse [cloud.ibm.com](https://cloud.ibm.com).

---

## 2. Selecione a conta correta

No canto superior direito, clique no seletor de conta.
Escolha a conta do programa IBM SkillsBuild (não a sua conta pessoal).

---

## 3. Acesse a instância watsonx Orchestrate

1. No painel IBM Cloud, vá em **Resource List**.
2. Localize o serviço **watsonx Orchestrate** (já provisionado pela equipe).
3. Clique no nome da instância.

---

## 4. Obtenha o Service Instance URL

1. Na página da instância, clique em **Manage** → **API details** (ou "View credentials").
2. Copie o campo **Service instance URL**.

> ⚠️ **Não derive a URL do endereço visual do navegador.**
> Use apenas o URL exibido em "API details".

Exemplo de formato:
```
https://api.au-syd.assistant.watson.cloud.ibm.com/instances/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

Configure no `.env`:
```
WXO_SERVICE_URL=https://...
```

---

## 5. Crie uma chave de API IAM

1. Acesse [Manage → IAM → API keys](https://cloud.ibm.com/iam/apikeys).
2. Clique em **Create an IBM Cloud API key**.
3. Dê um nome descritivo (ex: `sentinela-dev`).
4. Copie a chave imediatamente — ela não será exibida novamente.

Permissões necessárias na instância Orchestrate:
- `Manager` ou `Writer` no serviço watsonx Orchestrate.

Configure no `.env`:
```
WXO_API_KEY=chave-copiada-aqui
```

---

## 6. Importe ferramentas e agentes

Com `.env` configurado:

```bash
# Ferramentas
python scripts/importar_ferramentas.py

# Agentes
python scripts/importar_agentes.py
```

Anote os IDs exibidos e adicione ao `.env`:
```
WXO_AGENT_ANALISE_ID=uuid-exibido
WXO_AGENT_VALIDACAO_ID=uuid-exibido
```

---

## 7. Confirme a integração

```bash
python -c "from ibm.iam import token_atual; print('Token IAM:', token_atual()[:20], '...')"
```

Se retornar um token, a autenticação está funcionando.

---

## Notas sobre limitações do programa

- O ambiente SkillsBuild pode restringir o provisionamento de novas instâncias.
- Não presuma disponibilidade de Code Engine, hospedagem ou serviços pagos.
- Não provisione recursos para contornar restrições existentes.
- Se a instância Orchestrate não estiver acessível, execute o servidor em modo offline e documente a pendência.

---

## Variáveis locais utilizadas

| Variável | Descrição |
|---|---|
| `WXO_SERVICE_URL` | URL base da instância Orchestrate (de API details). **Não** termine com `/v1` — o adaptador adiciona o caminho `/v1/orchestrate/...` automaticamente. |
| `WXO_API_KEY` | Chave IAM com permissão na instância |
| `WXO_AGENT_ANALISE_ID` | ID do agente `sentinela_analise` |
| `WXO_AGENT_VALIDACAO_ID` | ID do agente `sentinela_validacao` |
| `WXO_TOOL_CONSULTAR_CONTEXTO_ID` | ID da ferramenta `consultar_contexto` |
| `WXO_TOOL_REGISTRAR_ANALISE_ID` | ID da ferramenta `registrar_analise` |
| `WXO_TOOL_EXECUTAR_DECISAO_ID` | ID da ferramenta `executar_decisao` |

---

## Estado da integração

O fluxo de risco médio foi validado com os agentes reais em 06/11/2026:
consulta do contexto, registro da análise, pergunta, confirmação sintética e decisão.
Os cinco recursos configurados no `.env` foram preservados. As três ferramentas
receberam os mesmos 45 IDs e os arquivos operacionais locais.

Consulte [VALIDACAO.md](VALIDACAO.md) para os comandos, resultados e limites.
Credenciais e backups ficam fora do Git. Para comparar a instância atual, use
`python scripts/sincronizar_orchestrate.py`. A opção `--aplicar` altera recursos
existentes e exige autorização da equipe.
