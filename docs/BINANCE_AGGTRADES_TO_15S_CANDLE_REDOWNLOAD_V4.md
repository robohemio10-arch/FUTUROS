# Binance USD-M Futures AggTrades → 15s Candle Redownload V4

## Objetivo

Reconstruir candles canônicos de 15 segundos para pesquisa quantitativa usando dados públicos da Binance USD-M Futures.

A abordagem V4 usa `aggTrades` públicos como fonte primária, porque o REST `/fapi/v1/klines?interval=1s` pode retornar `Invalid interval` em produção mesmo quando a documentação enumera `1s` como intervalo de kline. Para histórico, o arquivo público em `data.binance.vision` é a fonte canônica.

## Fonte

- Primária: `https://data.binance.vision/data/futures/um/daily/aggTrades/{SYMBOL}/{SYMBOL}-aggTrades-{YYYY-MM-DD}.zip`
- Fallback: `/fapi/v1/aggTrades`, apenas para janelas recentes quando o arquivo histórico não existir.

## Saída

Arquivos Parquet locais, não versionados:

```text
data/raw/binance_futures_klines_15s/{SYMBOL}/{SYMBOL}_15s_YYYYMMDD.parquet
```

Colunas principais:

- `symbol`
- `timeframe`
- `timestamp`
- `timestamp_ms`
- `open`
- `high`
- `low`
- `close`
- `volume`
- `quote_asset_volume`
- `number_of_agg_trades`
- `number_of_trades`
- `taker_buy_base_asset_volume`
- `taker_buy_quote_asset_volume`
- `source_interval`
- `source`
- `generated_at_utc`

## Safety

Esta rotina é estritamente read-only em relação à execução operacional:

- `paper_only=true`
- `shadow_only=true`
- `research_only=true`
- `live_trading_enabled=false`
- `order_submission_enabled=false`
- `exchange_private_access=false`
- `sends_orders=false`
- `changes_risk=false`
- `changes_model=false`
- `changes_training_dataset=false`

## Comandos

Preflight:

```powershell
python scripts\download_binance_futures_1s_resample_15s.py `
  --project-root . `
  --from-date 2026-01-05 `
  --to-date 2026-01-07 `
  --symbols BTCUSDT,ETHUSDT `
  --source archive_then_rest `
  --json `
  --no-write
```

Download de um dia:

```powershell
python scripts\download_binance_futures_1s_resample_15s.py `
  --project-root . `
  --from-date 2026-01-05 `
  --to-date 2026-01-05 `
  --symbols BTCUSDT,ETHUSDT `
  --source archive_then_rest `
  --json
```

Validações:

```powershell
python -m compileall -q scripts smartcrypto tests
python -m pytest tests\test_binance_1s_to_15s_redownload_v1.py -q
python scripts\scan_versioned_secrets.py --json
```


## Correção V4

- Aceita cabeçalhos reais do arquivo público histórico: `transact_time` e `is_buyer_maker`.
- Bloqueia fallback REST para backfills históricos quando o arquivo archive falhar, evitando candles parciais.
- Mantém saídas paper/shadow/research-only.
