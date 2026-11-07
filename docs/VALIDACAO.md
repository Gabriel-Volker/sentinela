# Validação da integração — 06/11/2026

## Correções

- O serviço confere operação e run sob o mesmo lock usado para aplicar o resultado.
  Uma resposta antiga não substitui a operação atual, inclusive enquanto o novo run
  ainda está sendo criado. Dois leitores não registram a mesma pergunta duas vezes.
- Falhas remotas e resultados inválidos encerram o acompanhamento como `pendente`.
  O campo `falha_integracao` informa a fase e o motivo. Não há decisão nem comprovante inventado.
- Uma resposta final isolada não autoriza nem bloqueia. O backend exige chamadas
  bem-sucedidas das ferramentas na própria run e confere transação, fase e hash.
- A interface ignora consultas de gerações ou revisões antigas. Uma consulta atrasada
  não apaga a resposta do cliente nem reabre a pergunta anterior.
- O timeout HTTP recebe o orçamento restante da espera local. Uma execução remota
  ainda ativa mantém seus IDs e pode ser consultada depois.
- As ferramentas usam os schemas de `fraude/contratos.py`. Dados ausentes, transação
  desconhecida e evidências inválidas não produzem comprovantes de sucesso.

## Verificações locais

Python 3.13.18, sem credenciais e com conexões externas bloqueadas:

```text
python -m pytest tests/ -q
189 passed

node --test tests/frontend.test.cjs
2 passed

python -m ruff check fraude/ ibm/ web/rotas.py web/servidor.py ferramentas/ scripts/
All checks passed

python -m ruff format --check fraude/ ibm/ web/rotas.py web/servidor.py ferramentas/ scripts/
26 files already formatted
```

Também passaram a verificação de sintaxe dos três arquivos JavaScript e `git diff --check`.
Os testes novos reproduziram as falhas de concorrência e de resultados terminais
antes da correção.

Os testes de pacotes extraem cada ZIP em um diretório temporário e executam a função,
os schemas e os arquivos reais em um processo isolado. Cada ferramenta é exercitada
com os 45 IDs. Somente o decorador do SDK externo é substituído nesse teste local.
O teste na IBM descrito abaixo executa também o SDK e o runtime remotos.

## Sincronização remota

```text
python scripts/sincronizar_orchestrate.py --aplicar
```

Os três pacotes foram baixados novamente após o envio. Resultado por ferramenta:

| Ferramenta | IDs locais encontrados remotamente | Código e arquivos iguais |
|---|---:|---|
| consultar_contexto | 45/45 | Sim |
| registrar_analise | 45/45 | Sim |
| executar_decisao | 45/45 | Sim |

A comparação inclui os três JSONs operacionais completos e todos os arquivos dos ZIPs.
Os nomes, instruções, variáveis de contexto, modelo e vínculos dos dois agentes
coincidiram com os YAMLs e os IDs configurados no `.env`.
Foi preservado o modelo remoto existente: `bedrock/us.anthropic.claude-sonnet-4-6`.
Nenhum recurso ou ID foi criado. O catálogo local não foi regenerado.

## Fluxo real

```text
python scripts/verificar_fluxo_orchestrate.py --transacao TX-L9YKVZ1B --resposta "Sim, fui eu. Reconheço a compra na Ticketmaster em Brasília."
```

O comando usa o servidor, as rotas, o coordenador, o serviço, IAM, a Runs API e os
dois agentes reais. A resposta do cliente e as operações bancárias são simuladas.
O fluxo foi executado duas vezes. Na execução final, após a última sincronização:

| Etapa | Resultado | Duração observada |
|---|---|---:|
| POST analisar | Risco médio; validação iniciada em segundo plano | 22,742 s |
| Acompanhamento da primeira pergunta | `aguardando_cliente`, pergunta presente no JSON HTTP | cerca de 8,2 s após o parecer |
| POST responder | `autorizado` | 14,315 s |

O backend verificou os registros de análise, pergunta e decisão. As chamadas remotas
`consultar_contexto`, `registrar_analise` e `executar_decisao` completaram o fluxo.

## Limites

- Os tempos são observações de duas execuções, não uma garantia ou um benchmark.
  A análise da execução anterior levou 30,358 s e a resposta final, 9,878 s.
- O acompanhamento do script real consulta a cada 2 s. A interface consulta a cada
  8 s e pode acrescentar esse intervalo à exibição do resultado.
- Os caminhos de baixo risco, alto risco, negação e ausência de resposta têm testes
  locais. Esta validação real cobre o caminho de risco médio com confirmação.
- Os testes JavaScript executam `app.js` e o diagnóstico de `ui.js`. Não foi feita
  nova inspeção visual em navegador; esta alteração não redesenha o layout.
- A suíte local não mede precisão do classificador, disponibilidade da IBM ou desempenho em carga.
- Hospedagem e persistência durável continuam fora da entrega atual.

## Recuperação da sincronização

Os backups anteriores ao envio ficam em `.local/orchestrate/<data-UTC>/`, ignorados
pelo Git. A sincronização pode falhar entre dois recursos; nesse caso, interrompa o
teste real e execute a comparação sem `--aplicar` para identificar a divergência.
Corrija a causa e repita a sincronização autorizada. Ela preserva os mesmos IDs e
verifica o resultado por novo download.

Para restaurar uma versão anterior, use seus YAMLs, código e dados como um conjunto
compatível e sincronize os mesmos recursos. Preserve os backups para recuperação
manual pela API de ferramentas (`POST tools/{id}/upload`) e agentes (`PATCH agents/{id}`).
Restaurar código não restaura a sessão em memória. Nunca envie `.env`, tokens ou
backups remotos para o GitHub.
