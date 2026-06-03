# Market Data Health: Spread, Liquidity e Latency Guards

Esta branch adiciona uma auditoria read-only de saúde de dados de mercado para o FUTUROS/SmartCrypto. O objetivo é bloquear ou alertar sobre dados stale, spread alto, baixa liquidez, slippage estimado alto, latência alta e divergência WebSocket/REST antes que qualquer camada de decisão confie nesses dados.

## Escopo

Arquivos principais:

- `smartcrypto/market/market_data_health.py`
- `scripts/run_market_data_health_audit.py`

A auditoria aceita inputs locais em Parquet, CSV, JSON e JSONL. Ela nunca acessa exchange, não importa `ccxt`, não envia ordens e não altera signal producer, RiskManager, Freqtrade DB, registry, modelos ou datasets oficiais.

## Fontes

Quando disponíveis, o auditor avalia:

- candles;
- ticker;
- order book snapshot;
- trades;
- WebSocket heartbeat;
- REST snapshot.

Arquivos ausentes são tratados como `missing_data` ou warning quando opcionais. Em `--strict`, a ausência de input mínimo bloqueia.

## Métricas

O relatório contém, por símbolo:

- `last_candle_age_seconds`
- `last_ticker_age_seconds`
- `last_order_book_age_seconds`
- `ws_heartbeat_age_seconds`
- `rest_snapshot_age_seconds`
- `ws_rest_timestamp_delta_seconds`
- `spread_bps`
- `top_of_book_depth`
- `estimated_slippage_bps`
- `liquidity_score`
- `latency_ms`

Também expõe `stale_data_count`, `divergence_count`, `blocked_symbols` e `warning_symbols`.

## Guardas

Guardas implementados:

- `DataFreshnessGuard`
- `SpreadGuard`
- `LiquidityGuard`
- `LatencyGuard`
- `WsRestDivergenceGuard`
- `OrderBookGuard`

O status por símbolo pode ser `ok`, `warning`, `blocked` ou `missing_data`. O status global é `blocked` se qualquer símbolo crítico bloquear ou se flags de segurança estiverem inseguras.

## CLI

```powershell
python scripts/run_market_data_health_audit.py `
  --candles data/runtime/market_candles.parquet `
  --ticker data/runtime/ticker.json `
  --order-book data/runtime/order_book.json `
  --trades data/runtime/trades.jsonl `
  --ws-heartbeat data/runtime/ws_heartbeat.json `
  --rest-snapshot data/runtime/rest_snapshot.json `
  --strict
```

Saída padrão:

- `data/reports/market_data_health_audit_report.json`

Esse relatório é runtime e não deve ser versionado.

## Limites configuráveis

O CLI aceita:

- `--max-candle-age-seconds`
- `--max-ticker-age-seconds`
- `--max-order-book-age-seconds`
- `--max-ws-heartbeat-age-seconds`
- `--max-spread-bps`
- `--min-top-depth`
- `--max-slippage-bps`
- `--max-latency-ms`
- `--max-ws-rest-delta-seconds`

## Segurança

Flags esperadas:

- `paper_only=true`
- `shadow_only=true`
- `live_trading_enabled=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `exchange_private_access=false`
- `sends_orders=false`
- `changes_risk=false`

Nenhum arquivo em `data/`, `models/`, `reports/`, parquet, SQLite, CSV, XLSX, logs ou evidence deve ser versionado.
