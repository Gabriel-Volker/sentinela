# AGENTS.md — Guia para o Sentinela

## Contexto do projeto

MVP de análise de fraudes em transações de cartão. Backend Python (stdlib HTTP), frontend HTML/CSS/JS, dois agentes reais do watsonx Orchestrate, três ferramentas remotas.

## Estrutura

```
fraude/         Contratos (contratos.py), estados, serviço, coordenador, repositório
ibm/            IAM (iam.py), adaptador Orchestrate (orchestrate.py)
ferramentas/    consultar_contexto, registrar_analise, executar_decisao
agentes/        analise.yaml, validacao.yaml
web/            servidor.py (HTTP), rotas.py (handlers), static/ (frontend)
scripts/        gerar_dados.py, importar_ferramentas.py, importar_agentes.py
tests/          test_contratos.py, test_dados.py, test_servico.py, test_http.py
dados/mock/     operacional/, referencias/, avaliacao/
docs/           ibm_skillsbuild.md
```

## Comandos essenciais

```bash
# Gerar dados
python scripts/gerar_dados.py

# Servidor local
python web/servidor.py

# Testes
python -m pytest

# Lint
python -m ruff check fraude/ ibm/ web/rotas.py web/servidor.py ferramentas/ scripts/
```

## Convenções críticas

- **Fonte única de schemas**: `fraude/contratos.py` — nunca duplicar.
- **Fakes IBM**: usar `ibm.orchestrate.AdaptadorFake` somente em testes ou execução de teste explicitamente identificada.
- **Gabarito**: nunca servido pelo frontend. Somente `dados/mock/avaliacao/` — não versionado.
- **Roteiro do cliente**: `roteiro_cliente` presente nas candidatas operacionais para o simulador, mas NUNCA exposto ao frontend ou aos agentes.
- **Injeção de dependências**: `Coordenador(svc, ibm, agente_analise_id=..., agente_validacao_id=...)`.
- **Estado em memória**: `ServicoCasos` não persiste. Reiniciar o servidor perde tudo.
- **context_variables**: enviadas na raiz da requisição Runs API (`payload["context_variables"] = {...}`).

## Áreas críticas

| Área | Arquivo | Atenção |
|---|---|---|
| Validação de contratos | `fraude/contratos.py` | `extra="forbid"`, versão obrigatória |
| Comprovantes | `fraude/coordenador.py` | Verificar antes de atualizar caso |
| Ausência de resposta | `fraude/coordenador.py` → `_processar_resultado_validacao` | Nunca autoriza |
| Limite de perguntas | `fraude/servico.py` → `MAX_PERGUNTAS = 3` | Converte para pendente |
| Segurança estática | `web/servidor.py` → `_ARQUIVOS_PERMITIDOS` | Lista explícita |
| Separação de dados | `scripts/gerar_dados.py` | Rotulo/cenário nunca na pasta operacional |

## Integração IBM

- Mantenha as credenciais em `.env`, ignorado pelo Git.
- Use `python scripts/sincronizar_orchestrate.py` para comparar os cinco recursos existentes.
- `--aplicar` altera a IBM e exige autorização da tarefa. Salva backups ignorados em `.local/orchestrate/`.
- Preserve os IDs atuais ao atualizar. Não regenere dados para ajustar um pacote remoto antigo.
- Cada resultado só pode ser aplicado uma vez, à operação e ao run correspondentes, sob o lock do caso.
- Exija chamada e retorno da ferramenta no `step_history` da própria run. JSON final isolado não executa decisão.
- Reenvie `context_variables` em cada run, mesmo ao reutilizar uma thread.
- Falhas terminais geram `falha_integracao` e estado `pendente`, sem comprovante ou resposta de agente inventados.
- A suíte rápida não exige SDK ou credenciais. Teste a interface com `node --test tests/frontend.test.cjs`.
- O teste real é separado: `scripts/verificar_fluxo_orchestrate.py`. Consulte `docs/VALIDACAO.md`.

Nunca apresente execução fake como integração IBM validada.
