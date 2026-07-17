# Market Features Rematerialization and First Training Runs V1

## Objective

This package rematerializes entry-time market features from the latest fully
closed five-minute candle and runs the first ephemeral research challenger
evaluations. It is paper/shadow/research-only and has no operational authority.

The canonical inputs are:

- `data/trades/trades_master.parquet` for Master research labels;
- `data/features/market_features_60d.parquet` for local public market features;
- `data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite` for the external
  paper holdout, read through the existing query-only snapshot adapter.

## Point-in-time contract

The market feature timestamp is the candle open timestamp. For a five-minute
candle:

```text
available_at_utc = candle_timestamp_utc + 5 minutes
available_at_utc <= trade_open_time_utc
0 <= feature_age_seconds < 300
```

The strict age bound prevents a missing candle from being silently filled with
an older row. The implementation does not forward-fill and does not impute.
Rows with missing market values receive an individual blocker.

One-minute data is explicitly blocked for this training period because the
confirmed paper coverage is zero. It is never substituted with five-minute data
under a one-minute name.

## Feature boundary

Only contemporaneous numeric five-minute market fields enter the model matrix.
PnL, MFE, MAE, close time, exit price, exit reason, future returns, targets and
labels are prohibited as features. PnL is used only as an outcome for evaluation.
Indicators are recomputed from OHLCV inside contiguous five-minute segments;
pre-existing partially-null indicator columns are not trusted or filled.

The four ephemeral model families are:

- Logistic Regression with scaling;
- Extra Trees;
- Random Forest;
- Histogram Gradient Boosting.

No imputer exists in any model pipeline. Models remain in memory and are not
serialized, registered, promoted or connected to Qlib/IA Shadow/Freqtrade.

## Dataset separation

Master rows are candidates for fitting after row-level validation. Paper rows
are an external holdout and always report:

```text
paper_rows_used_for_fit=0
paper_rows_used_for_calibration=0
```

For paper evaluation, the Master fit is additionally restricted to trades that
closed before the first paper open time minus the embargo. This avoids fitting
on chronologically later Master observations.

The historical `expected_rows=3504` contract is reconciled against the current
canonical `3562` rows. The delta of 58 is reported as unresolved aggregate
lineage evidence. All 3562 rows are retained for row-level validation and the
silent discard count is always zero.

## Evaluation

Walk-forward folds are expanding and temporal. Training intervals that overlap
the test start or its embargo are purged. Backtests use the observed net PnL as
the authoritative post-cost outcome; observed fees remain diagnostic and no
synthetic gross PnL is reverse-engineered.

Monte Carlo uses contiguous block bootstrap with a fixed seed to preserve local
temporal dependence. Models are ranked by net PnL, profit factor, expectancy,
drawdown and fold stability. Ranking never grants promotion eligibility.

Qlib is optional and fail-closed. An unavailable package or a missing provider
configuration produces a controlled blocker and never starts runtime services.

## CLI

No-write is the default. A complete research probe is:

```powershell
python scripts/run_market_features_rematerialization_and_first_training_runs_v1.py `
  --project-root . `
  --allow-paper-read `
  --rematerialize-features `
  --run-baselines `
  --run-supervised-training `
  --run-qlib-training `
  --run-walkforward `
  --run-backtest `
  --run-monte-carlo `
  --evaluate-paper-holdout `
  --no-write `
  --json
```

`--write-research-artifacts` is the only write opt-in. It may write ignored
Parquet evidence below `data/research/market_features_first_training_runs_v1`
and JSON/Markdown reports below `data/reports`. It never writes runtime,
SQLite, active registries, models, signals, Master, risk or orders.

## Safety boundary

The pipeline does not call an exchange, read private credentials, submit orders,
alter risk, promote a model, write an active registry, update Qlib runtime, or
change Freqtrade. A warning or blocked component remains visible and is never
promoted to operational readiness.
