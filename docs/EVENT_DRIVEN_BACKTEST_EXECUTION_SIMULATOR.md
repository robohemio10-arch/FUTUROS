# Event-Driven Backtest Execution Simulator

## Objetivo

O backtest event-driven simula execucao realista de sinais/trades usando candles
locais. Ele aplica custos, spread, slippage, latencia, limite de liquidez,
partial fills, rejeicoes simuladas, timeouts e no-fills sem acessar exchange,
sem enviar ordens e sem alterar a operacao paper/shadow.

## Entradas

O simulador aceita sinais e candles locais em:

- parquet;
- csv;
- json;
- jsonl.

Campos padrao:

- `timestamp`;
- `symbol`;
- `side`;
- `close` nos candles.

O preco de entrada e sempre o primeiro candle disponivel em ou depois de:

```text
timestamp_da_decisao + latency_seconds
```

O simulador nunca usa preco anterior ao timestamp da decisao.

## Custos E Execucao

O modelo de execucao considera:

- fee maker/taker simplificado via `--fee-bps`;
- spread via `--spread-bps`;
- slippage via `--slippage-bps`;
- latencia via `--latency-seconds`;
- limite de liquidez via `--liquidity-cap`;
- partial fill via `--partial-fill-ratio`;
- rejeicao simulada por campos opcionais do sinal;
- timeout simulado por campos opcionais do sinal;
- no-fill quando nao ha candle futuro elegivel.

## Metricas

O relatorio inclui:

- `total_signals`;
- `executed_trades`;
- `skipped_trades`;
- `rejected_trades`;
- `partial_fills`;
- `no_fills`;
- `gross_pnl`;
- `net_pnl`;
- `total_fees`;
- `total_slippage_cost`;
- `total_spread_cost`;
- `win_rate`;
- `average_win`;
- `average_loss`;
- `expectancy`;
- `profit_factor`;
- `max_drawdown`;
- `equity_curve_summary`;
- `baseline_summary`;
- `execution_quality_summary`.

## Uso

```powershell
python .\scripts\run_event_driven_backtest.py `
  --signals data/reports/ai_shadow_model_decisions.jsonl `
  --candles data/features/market_features_60d.parquet `
  --report data/reports/event_driven_backtest_report.json `
  --timestamp-column timestamp `
  --symbol-column symbol `
  --side-column side `
  --price-column close `
  --fee-bps 2 `
  --spread-bps 4 `
  --slippage-bps 3 `
  --latency-seconds 1 `
  --liquidity-cap 1000 `
  --partial-fill-ratio 1.0 `
  --seed 42 `
  --strict
```

## Bloqueios

O simulador retorna `status=blocked` quando:

- sinais estao ausentes;
- candles estao ausentes;
- timestamps ou colunas essenciais estao ausentes;
- candles estao fora de ordem;
- candles duplicados aparecem sem politica de deduplicacao;
- timestamp de execucao seria anterior ao timestamp de decisao;
- preco de execucao esta indisponivel;
- safety flags sao inseguras;
- `live_trading_enabled=true`;
- `order_submission_enabled=true`;
- `real_order_submission_enabled=true`;
- `exchange_private_access=true`;
- `sends_orders=true`;
- `changes_risk=true`.

## Artefato Runtime

Relatorio padrao:

```text
data/reports/event_driven_backtest_report.json
```

Esse arquivo e runtime e nao deve ser versionado. Arquivos em `data/`,
`models/`, `reports/`, parquet, sqlite, csv, xlsx, logs e evidence permanecem
fora do git.

## Garantias De Seguranca

Este fluxo e paper/shadow only:

- nao habilita live trading;
- nao habilita `ORDER_SUBMISSION_ENABLED`;
- nao habilita `REAL_ORDER_SUBMISSION_ENABLED`;
- nao acessa exchange privada;
- nao envia ordens;
- nao altera Freqtrade DB;
- nao altera `trades_master`;
- nao altera `training_dataset.parquet`;
- nao altera signal producer;
- nao altera runtime Qlib;
- nao altera registry automaticamente;
- nao promove modelo automaticamente;
- nao altera modelos;
- nao altera risco;
- nao altera Docker;
- nao altera `.env`.
