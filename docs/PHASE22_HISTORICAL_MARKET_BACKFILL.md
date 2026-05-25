# Fase 22 — Historical Market Backfill for Imported Trades

## Objetivo

Corrigir a diferença entre `trades_master` e `training_dataset`.

A Fase 22 baixa candles históricos de 1 minuto para BTCUSDT e ETHUSDT na Binance USDT-M Futures desde 06/01/2026 até hoje, reconstrói as features de mercado e roda novamente a reconstrução da Fase 5.

## Endpoint usado

A fase usa o endpoint público de candles da Binance USDT-M Futures:

`GET /fapi/v1/klines`

A documentação oficial da Binance informa que o endpoint aceita `symbol`, `interval`, `startTime`, `endTime` e `limit`, com limite máximo de 1500 candles por chamada.

## Comando principal

```powershell
.\paper_controlado_fase_22\RUN_PHASE22_FULL_BACKFILL.ps1 -StartDate "06/01/2026" -EndDate "today"
```

## Resultado esperado

Após a execução:

- `market_features_60d.parquet` passa a conter candles históricos adicionais.
- `trade_enriched.parquet` deve aumentar de linhas.
- `training_dataset.parquet` deve aumentar de linhas.
- `phase22_output_summary.json` mostra cobertura dos arquivos.
