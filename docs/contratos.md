# Contratos e Estados — Sentinela

## Contrato de Análise (versao: 2)

```json
{
  "versao": 2,
  "transacao_id": "TX-000001",
  "risco": "medio",
  "acao": "validar",
  "evidencias": [
    {
      "caminho": "/transacao/valor",
      "descricao": "Valor da compra em análise."
    }
  ],
  "informacoes_ausentes": [
    "Reconhecimento da compra pelo cliente."
  ],
  "justificativa": "Justificativa baseada no contexto consultado."
}
```

### Correspondências obrigatórias

| risco | acao |
|---|---|
| baixo | autorizar |
| medio | validar |
| alto | bloquear |

---

## Contrato de Validação (versao: 2)

```json
{
  "versao": 2,
  "transacao_id": "TX-000001",
  "desfecho": "aguardar_cliente",
  "pergunta": "Você reconhece esta compra?",
  "evidencias": [
    {
      "caminho": "/transacao/valor",
      "descricao": "Valor apresentado para confirmação."
    }
  ],
  "justificativa": "A compra precisa de confirmação."
}
```

### Desfechos válidos

| desfecho | pergunta | significado |
|---|---|---|
| `aguardar_cliente` | obrigatória | Agente aguarda resposta |
| `autorizar` | null | Desfecho final positivo |
| `bloquear` | null | Desfecho final negativo |
| `pendente` | null | Sem decisão conclusiva |

---

## Estados do caso

```
novo
 │
 └─► analisando
       │
       ├─► autorizado          (baixo risco)
       ├─► bloqueado           (alto risco)
       ├─► validando           (médio risco → auto-dispara agente de validação)
       │     │
       │     ├─► aguardando_cliente
       │     │     │
       │     │     └─► validando → (ciclo até desfecho final ou limite)
       │     │
       │     ├─► autorizado
       │     ├─► bloqueado
       │     └─► pendente
       │
       └─► pendente_execucao   (timeout local — run preservada)
             │
             └─► (qualquer estado acima, via consultar)
```

### Estados terminais

- `autorizado`
- `bloqueado`
- `pendente`

---

## Evidências

Cada evidência contém:
- `caminho`: JSON Pointer (RFC 6901) apontando para um campo **escalar** da transação.
- `descricao`: texto explicativo para o analista.
- `valor`: resolvido pelo backend — **não preenchido pelo agente**.

O agente não copia valores; o backend resolve e acrescenta.

---

## Rejeições automáticas

- JSON inválido ou campos duplicados
- Campos extras ou obrigatórios ausentes
- Tipos incorretos
- Transação divergente
- Ação incompatível com o risco
- Evidência com caminho inexistente
- Evidência que aponta para um objeto completo
- Desfecho final com pergunta preenchida
- Ausência de resposta com desfecho autorizando


## Concorrência e falhas de integração

As respostas HTTP de casos incluem `revisao`, um inteiro crescente por sessão.
A interface descarta revisões antigas e consultas iniciadas antes de uma nova resposta.
O serviço aplica cada resultado uma vez, sob o lock, somente quando `operacao_id`
e `run_id` correspondem à operação reservada. O ID vazio durante a criação de um
novo run não permite aplicar o resultado anterior.

`failed`, `cancelled`, `error` e resultados concluídos sem contrato, evidências ou
comprovantes válidos encerram o caso como `pendente`. O campo `falha_integracao`
informa `fase`, `codigo` e `mensagem`. Ele representa uma falha técnica, não um
parecer do agente. A interface para de consultar e pede revisão humana.

Um timeout local preserva `pendente_execucao` e os IDs. O orçamento restante chega
ao timeout HTTP de cada consulta. Esse limite não cancela a IBM nem constitui um
prazo absoluto para DNS, renovação IAM e todas as leituras da conexão.

Os comprovantes são extraídos de `result.data.message.step_history`. O backend
vincula chamada e retorno pelo `tool_call_id`, exige sucesso, confere a transação
e compara o SHA-256 do contrato normalizado com o retorno da ferramenta.
