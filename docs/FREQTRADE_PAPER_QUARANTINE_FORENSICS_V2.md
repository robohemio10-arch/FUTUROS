# Freqtrade Paper Quarantine Forensics V2

## Objetivo

O Bloco 1A.2 investiga exclusivamente os trades paper `141`, `221`, `234`, `258` e `561`
que permaneceram em quarentena no enriquecimento SQLite autoritativo. A analise e descritiva,
read-only e nao altera o adapter, o fingerprint V2 ou qualquer artefato operacional.

## Fonte e acesso

A unica fonte permitida e:

`data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite`

O caminho `freqtrade/user_data/tradesv3.paper.sqlite` permanece explicitamente nao
autoritativo e e rejeitado antes de qualquer leitura.

O leitor:

1. calcula SHA-256 do DB, WAL e SHM;
2. copia os artefatos para diretorio temporario;
3. abre a copia por URI `mode=ro`;
4. ativa e confirma `PRAGMA query_only=ON`;
5. inspeciona apenas `trades`, `orders` e `trade_custom_data`, cujas relacoes por `id` e
   `ft_trade_id` foram comprovadas;
6. fecha e remove a copia;
7. recalcula os hashes originais e bloqueia se houver mudanca.

Nenhum candle, `close_rate_requested`, interpolacao ou PnL reverso e usado.

## Ordens executadas

Uma ordem so e considerada fill autoritativo quando:

- `status=closed`;
- `filled > 0`;
- `average > 0`;
- `order_filled_date` esta presente.

Ordens `canceled`, `cancelled`, `rejected`, `expired`, abertas sem fill ou com `filled=0`
sao ignoradas e registradas. Um texto em `ft_cancel_reason` nao invalida sozinho um fill que
foi persistido como fechado, integralmente preenchido e com timestamp de execucao.

## Formulas candidatas

As formulas sao avaliadas por contrato, nunca selecionadas por proximidade ao net esperado.

### Trade summary

`trade_summary_single_amount_v1` usa `open_rate`, `close_rate`, `amount` e `contract_size`.
Ela preserva o residual original do Bloco 1A.1.

### Filled orders weighted average

`filled_orders_weighted_average_v1` calcula:

```text
weighted_entry = sum(entry.average * entry.filled) / sum(entry.filled)
weighted_exit  = sum(exit.average  * exit.filled)  / sum(exit.filled)

long gross  = (weighted_exit - weighted_entry) * filled_exit_quantity * contract_size
short gross = (weighted_entry - weighted_exit) * filled_exit_quantity * contract_size

effective_open_fee  = fee_open_cost * leverage
effective_close_fee = fee_close_cost
trading_fee          = effective_open_fee + effective_close_fee
funding_fee          = -funding_fees
net                  = gross - trading_fee - funding_fee
residual             = abs(net - close_profit_abs)
```

Recuperacao exige simultaneamente:

- quantidade de entrada preenchida igual a quantidade de saida preenchida;
- ambas iguais a `trades.amount` dentro de `1e-8`;
- `realized_profit` compatível com `close_profit_abs`;
- residual financeiro menor ou igual a `1e-8`;
- nenhuma evidencia de execucao obrigatoria ausente.

## Resultado observado

| Trade | Entry filled | Exit filled | Ordens | Residual original | Residual recuperado | Decisao |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 141 | 0.060 | 0.120 | 3 | 1.0007150399999999 | n/a | accounting unexplained |
| 221 | 0.064 | 0.064 | 5 | n/a | 0.0000000040383999966 | recovered authoritatively |
| 234 | 0.061 | 0.061 | 3 | n/a | 0.0000000040 | recovered authoritatively |
| 258 | 0.060 | 0.120 | 3 | 1.28547708 | n/a | accounting unexplained |
| 561 | 0.056 | 0.112 | 3 | 1.013735738000000004 | n/a | accounting unexplained |

Os trades 221 e 234 possuem saídas fechadas, integralmente preenchidas, com preço médio e
timestamp persistidos nas ordens. Os trades 141, 258 e 561 possuem duas saídas preenchidas
para uma unica entrada, totalizando duas vezes `trades.amount`; `trade_custom_data` nao contem
evidencia de position adjustment. Eles permanecem em quarentena.

## Execucao

```powershell
python .\scripts\analyze_freqtrade_paper_quarantine_forensics_v2.py `
  --project-root . `
  --json
```

O CLI nao oferece modo de escrita nem aceita IDs arbitrarios.

## Fora de escopo

- alterar tolerancias ou `fingerprint_spec_v2`;
- aplicar recuperacoes no adapter ou no Master;
- comparar com Trader Master;
- writer, importacao, backup ou backfill;
- Freqtrade runtime, Qlib, IA Shadow ou Strategy Factory;
- acesso privado a exchange ou envio de ordens.
