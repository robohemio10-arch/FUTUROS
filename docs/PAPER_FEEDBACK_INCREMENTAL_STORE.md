# Paper Feedback Incremental Store

## Objetivo

A store incremental persiste trades paper fechados exportados pela Fase 14 em um
artefato deduplicado e auditavel, sem escrever diretamente em `trades_master`.

Ela e o primeiro bloco para microbatches de treino paper/shadow: a Fase 14 coleta
feedback fechado continuamente, e esta store guarda apenas registros novos.

## Fonte E Saida

Input padrao:

```text
data/trades/inbox/freqtrade_paper_closed_trades.csv
```

Output principal:

```text
data/feedback/paper_closed_trades_incremental.parquet
```

Relatorio:

```text
data/reports/paper_feedback_incremental_store_report.json
```

## Politica De Deduplicacao

A deduplicacao usa a politica `order_id_first_then_fingerprint`:

1. `order_id` valido e nao vazio e a chave primaria;
2. se `order_id` estiver ausente, vazio, `NaN` ou `None`, o script usa
   fingerprint composto;
3. `order_id` vindo de Excel como `123.0` e normalizado para `123`;
4. registros existentes nunca sao removidos.

O fingerprint composto usa simbolo, lado, horarios, precos e PnL. Ele e fallback
para feedback paper que ainda nao possua `order_id`.

## Schema Minimo

A store grava pelo menos:

```text
order_id
moeda
fechar_side
horario_abertura
horario_fechamento
preco_abertura
preco_fechamento
pnl_fechado
taxa_lucros_perdas_fechados_pct
exit_reason
source
imported_at_utc
record_hash
```

`exit_reason` e preservado quando existir na fonte. Se nao existir, fica vazio.

## Execucao

Execucao unica, default seguro:

```powershell
python .\scripts\update_paper_feedback_incremental_store.py
```

Com caminhos explicitos:

```powershell
python .\scripts\update_paper_feedback_incremental_store.py `
  --input data/trades/inbox/freqtrade_paper_closed_trades.csv `
  --output data/feedback/paper_closed_trades_incremental.parquet `
  --report data/reports/paper_feedback_incremental_store_report.json
```

Modo estrito:

```powershell
python .\scripts\update_paper_feedback_incremental_store.py --strict
```

No modo `--strict`, o script bloqueia se colunas obrigatorias do feedback fechado
estiverem ausentes.

## Relatorio

O relatorio expoe:

- `status`
- `reason`
- `input_rows`
- `existing_rows`
- `new_rows`
- `duplicate_rows`
- `final_rows`
- `duplicate_by_order_id_rows`
- `duplicate_by_fingerprint_rows`
- `missing_order_id_rows`
- `min_close_ts`
- `max_close_ts`
- `symbols`
- `sides`
- `output_path`
- flags de seguranca paper/shadow

## Garantias

- nao altera `trades_master`;
- nao toca no DB operacional do Freqtrade;
- nao chama exchange privada;
- nao envia ordens;
- nao habilita live;
- escreve apenas a store incremental e o relatorio configurado;
- nao versionar `data/feedback`, `data/reports` ou qualquer artefato runtime.
