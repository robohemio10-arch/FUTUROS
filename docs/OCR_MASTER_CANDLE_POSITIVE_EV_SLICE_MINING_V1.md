# OCR Master Candle Positive EV Slice Mining V1

## Objetivo

Minerar, em modo **research-only/read-only**, slices positivos no dataset alinhado `trades_master.xlsx + candles reais 1m`.

A branch não promove regras, não altera paper, não altera Freqtrade, RiskManager, Qlib, IA Shadow, modelos, SQLite, runtime, ordens ou exchange privada.

## Fontes

A leitura real só ocorre com `--allow-runtime-read`.

Fontes esperadas:

- `data/trades/trades_master.xlsx`
- `data/raw/binance_futures_klines/BTCUSDT_1m_20251230_20261208.csv`
- `data/raw/binance_futures_klines/ETHUSDT_1m_20251230_20261208.csv`

## O que é minerado

Dimensões simples e compostas:

- `symbol_norm`
- `side_norm`
- `hour`
- `duration_bucket`
- `regime_bucket`
- `symbol_norm + side_norm`
- `symbol_norm + hour`
- `side_norm + hour`
- `symbol_norm + regime_bucket`
- `side_norm + regime_bucket`

## Critérios de candidato positivo

Um slice só entra em `top_positive_candidates` se:

- `trade_count >= min_trade_count`
- `net_pnl > 0`
- `profit_factor > baseline_profit_factor`
- `mean_pnl > baseline_mean_pnl`
- `win_rate >= baseline_win_rate`
- `max_day_concentration <= max_day_concentration`

Mesmo quando encontrado, o candidato permanece bloqueado:

- `ready_for_candidate_registry=false`
- `paper_observation_allowed=false`
- `can_promote_rules=false`
- `operational_authority=false`

## Comando default

```powershell
python .\scripts\build_ocr_master_candle_positive_ev_slice_mining_v1.py `
  --project-root . `
  --no-write `
  --json
```

## Comando com leitura real explícita

```powershell
python .\scripts\build_ocr_master_candle_positive_ev_slice_mining_v1.py `
  --project-root . `
  --allow-runtime-read `
  --trades-master ".\data\trades\trades_master.xlsx" `
  --candle-root ".\data" `
  --no-write `
  --json
```

## Gates

A saída deve permanecer:

- `status=blocked`
- `decision=MANTER_EM_RESEARCH`
- `research_only=true`
- `read_only=true`
- `operational_authority=false`
- `ready_for_candidate_registry=false`
- `paper_observation_allowed=false`
- `updates_freqtrade=false`
- `updates_risk_manager=false`
- `updates_qlib_runtime=false`
- `updates_ai_shadow_runtime=false`
- `sends_orders=false`
