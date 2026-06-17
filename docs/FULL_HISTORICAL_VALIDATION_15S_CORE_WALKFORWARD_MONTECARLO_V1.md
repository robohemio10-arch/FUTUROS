# Full Historical Validation 15s Core — Walk-Forward, Monte Carlo e Custos

## Escopo

Esta entrega adiciona uma camada de validação quantitativa `research_only` para a base histórica SMART FUTUROS com candles canônicos Binance USD-M Futures em 15 segundos derivados de `aggTrades` públicos.

O auditor aceita somente arquivos locais sob:

```text
data/raw/binance_futures_klines_15s/{SYMBOL}/{SYMBOL}_15s_YYYYMMDD.parquet
```

Artefatos Bitradex, relatórios de auditoria, arquivos rejeitados, anomalias e features derivadas não satisfazem a cobertura canônica.

## Invariantes

```text
paper_only=true
shadow_only=true
runtime_mode=paper
live_trading_enabled=false
live_release_allowed=false
canary_release_allowed=false
order_submission_enabled=false
real_order_submission_enabled=false
exchange_private_access=false
sends_orders=false
changes_risk=false
changes_model=false
changes_training_dataset=false
research_only=true
```

## Fontes

### Candles 15s

Fonte canônica local: `data/raw/binance_futures_klines_15s/`.

Cada arquivo diário deve conter:

```text
5760 linhas
median_interval_seconds=15.0
max_interval_seconds=15.0
min_timestamp=YYYY-MM-DDT00:00:00Z
max_timestamp=YYYY-MM-DDT23:59:45Z
colunas: timestamp, symbol, open, high, low, close, volume
```

### Trades

A fonte preferida para validação com mais de 3000 trades é:

```text
data/features/trade_enriched.parquet
```

Essa base contém `symbol`, `open_ts`, `pnl`, `return_pct`, `mfe_pct`, `mae_pct` e metadados normalizados. Caso esteja ausente, o runner tenta fontes secundárias, mas bloqueia se não encontrar timestamp e PnL utilizáveis.

## Comandos

```powershell
python scripts\audit_15s_candle_coverage.py --project-root . --from-date 2026-01-05 --timeframe 15s --json

python scripts\run_full_historical_validation_15s.py --project-root . --from-date 2026-01-05 --timeframe 15s --json --no-write
```

## Componentes avaliados

- cobertura canônica Binance 15s;
- integridade da base de trades;
- custo de execução simulado;
- Monte Carlo antes e depois de custos;
- block bootstrap;
- walk-forward temporal com embargo;
- presença/reconstrutibilidade de MAE/MFE.

## Política de readiness

Mesmo quando `status=ok` para evidência de pesquisa, a seção `readiness` permanece `blocked`. Esta validação não libera live, canary, envio de ordens, mudança de risco, promoção de modelo ou alteração de dataset oficial.
