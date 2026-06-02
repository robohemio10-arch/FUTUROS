# Incremental Training Microbatch

## Objetivo

O builder cria um microbatch incremental de treino a partir de trades paper
fechados e market features historicas operacionais. Ele nao treina modelo e nao
altera `training_dataset.parquet`.

Este artefato e uma camada intermediaria para futuros microbatches de treino
paper/shadow.

## Entradas

Feedback paper incremental:

```text
data/feedback/paper_closed_trades_incremental.parquet
```

Market features operacionais:

```text
data/features/market_features_60d.parquet
```

As market features operacionais nao podem conter `future_ret_*`. Se qualquer
coluna desse tipo aparecer, o builder bloqueia para evitar lookahead/leakage.

## Saidas

Microbatch:

```text
data/features/incremental_training_microbatch.parquet
```

Relatorio:

```text
data/reports/incremental_training_microbatch_report.json
```

## Join Temporal

Para cada trade fechado:

1. normaliza `moeda` para `symbol` no formato `BTCUSDT`/`ETHUSDT`;
2. normaliza `fechar_side` para `long`/`short`;
3. valida `horario_abertura`, `horario_fechamento` e `pnl_fechado`;
4. procura, por simbolo, a feature mais recente com timestamp menor ou igual a
   `horario_abertura`;
5. calcula `feature_age_seconds`;
6. registra `missing_feature_rows` quando nao houver feature compativel.

O builder nunca usa feature posterior ao horario de abertura do trade.

## Targets

O output inclui:

```text
target_profitable = 1 se pnl_fechado > 0, senao 0
target_return = taxa_lucros_perdas_fechados_pct
```

## Schema Minimo

O microbatch inclui:

```text
order_id
symbol
side
open_time_utc
close_time_utc
pnl_fechado
target_return
target_profitable
feature_timestamp_utc
feature_age_seconds
colunas numericas de market features
source_feedback_path
source_features_path
built_at_utc
record_hash
```

## Execucao

Default:

```powershell
python .\scripts\build_incremental_training_microbatch.py
```

Com caminhos explicitos:

```powershell
python .\scripts\build_incremental_training_microbatch.py `
  --feedback data/feedback/paper_closed_trades_incremental.parquet `
  --features data/features/market_features_60d.parquet `
  --output data/features/incremental_training_microbatch.parquet `
  --report data/reports/incremental_training_microbatch_report.json
```

Modo estrito:

```powershell
python .\scripts\build_incremental_training_microbatch.py --strict
```

No modo `--strict`, o builder bloqueia schema invalido, `future_ret_*`, output
vazio, timestamps invalidos e qualquer violacao point-in-time.

## Garantias

- nao escreve `data/features/training_dataset.parquet`;
- nao altera `trades_master`;
- nao treina modelo;
- nao toca no DB operacional do Freqtrade;
- nao chama exchange privada;
- nao envia ordens;
- mantem paper/shadow only;
- nao versionar `data/`, parquet, csv, sqlite, logs ou evidence.
