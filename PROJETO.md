# PROJETO.md — Escopo e Limites do Sentinela

## O que é o Sentinela

MVP universitário de análise de fraudes em transações de cartão de crédito, desenvolvido no contexto do IBM SkillsBuild. Demonstra a coordenação entre dois agentes reais do watsonx Orchestrate para classificar risco e validar transações suspeitas com o cliente.

---

## O que está implementado

- Seleção de cliente e compra com busca por texto (nome, cidade, código).
- Análise de risco via agente real do Orchestrate (baixo → autorizar, alto → bloquear).
- Encaminhamento de risco médio ao segundo agente de validação.
- Conversa com cliente em modo manual ou roteiro sintético turno a turno.
- Registro de autorização, bloqueio ou pendência (simulado).
- Exportação dos registros da sessão (JSON, sem credenciais ou gabarito).
- **Painel de sessão**: lista todos os casos abertos nesta execução (`GET /casos`).
- **Polling automático**: quando o estado é `pendente_execucao`, o frontend consulta o resultado a cada 8 s sem intervenção manual.
- **Navegação entre casos**: clique em qualquer caso da sessão para recarregar o painel.
- Três ferramentas remotas com contrato validado.
- Dois agentes com definição YAML e scripts de importação.
- 45 transações candidatas em 15 cenários com 3 personas fictícias.
- 60 transações históricas para contexto dos agentes.
- Split dev/teste: candidatas_dev (30), candidatas_teste (15), gabaritos separados.
- Separação física: operacional / referências / avaliação (gabarito).
- Testes de contratos, estados, concorrência, HTTP, pacotes e interface, sem credenciais IBM.
- CI com lint (ruff), verificação de formato e testes obrigatórios.
- Servidor HTTP com biblioteca padrão Python, sem frameworks externos.

---

## Fora do escopo (não implementado)

| Item | Razão |
|---|---|
| Integração com bancos ou pagamentos reais | Fora do escopo do MVP |
| WhatsApp e outros serviços de mensageria | Fora do escopo |
| Biometria facial | Fora do escopo |
| Treinamento de modelos | Fora do escopo |
| Classificador de fraude externo aos agentes | Os agentes fazem a classificação |
| Terceiro agente supervisor | Fora do escopo |
| RAG e banco vetorial | Fora do escopo |
| Cadastro de usuários e autenticação bancária | Fora do escopo |
| Persistência durável | Estado em memória — reiniciar perde o estado |
| Microsserviços | Arquitetura monolítica intencional |
| Hospedagem remota | Não há ambiente autorizado disponível |

---

## Pendências

1. **Avaliação ampliada da integração** — o fluxo real de risco médio foi validado em 06/11/2026.
   - Isso não mede a precisão do classificador nem garante a disponibilidade da IBM.
   - Evidências e comandos: ver `docs/VALIDACAO.md`.

2. **Hospedagem remota** — demonstração local é a entrega válida atual.

3. **Avaliação massiva** — não executar sem orçamento e autorização para as chamadas.

---

## Limites técnicos documentados

- Estado de sessão em memória: reiniciar o servidor perde todos os casos abertos.
- Exportações não restauram sessões automaticamente.
- O health check (`GET /health`) confirma apenas o processo local, não a integração IBM.
- Percentuais de risco gerados por LLM não são probabilidades comprovadas.
- O servidor só aceita conexões de `127.0.0.1` — não expor na internet.

---

## Versão

Código: consulte o commit da revisão ou do PR.
Dados sintéticos: semente `20260930`
SDK: `ibm-watsonx-orchestrate==2.19.1`
