# Market Features Rematerialization and First Training Runs V1

## Institutional boundary

This package rematerializes point-in-time five-minute market features and
evaluates ephemeral research challengers. It is paper/shadow/research-only. It
does not promote or serialize models, update a registry, change runtime or risk,
submit orders, or access a private exchange.

The canonical environment is exact and fail-closed:

| Component | Required version |
| --- | --- |
| Python | 3.11.15 |
| scikit-learn | 1.8.0 |
| joblib | 1.5.3 |

Another environment may run input validation, feature rematerialization,
lineage reconciliation, paper-set classification, and concept-drift diagnosis.
It may not fit models, calibrate thresholds, backtest, evaluate paper model
predictions, or run Monte Carlo. A mismatch is reported as
`canonical_training_environment_mismatch`.

## Inputs and point-in-time contract

Canonical inputs are `data/trades/trades_master.parquet`,
`data/features/market_features_60d.parquet`, and the paper snapshot
`data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite`. Paper access
requires `--allow-paper-read` and uses the existing query-only snapshot adapter.

For a candle whose timestamp is its open time:

```text
available_at_utc = candle_timestamp_utc + 5 minutes
available_at_utc <= trade_open_time_utc
0 <= feature_age_seconds < 300
```

Only closed, already available candles are joined. Gaps are never crossed by
forward fill and missing values are never imputed. One-minute data remains
blocked for this period because confirmed paper coverage is zero.

The historical `expected_rows=3504` is reconciled explicitly with the canonical
Master row count. Every delta row is reported; no row is silently discarded.

## Model input boundary

The model matrix contains contemporaneous numeric OHLCV/indicator fields plus
deterministic representations of these known-at-entry values:

- `symbol` and `side`;
- `entry_hour_utc` and `entry_day_of_week`;
- `feature_age_seconds`;
- `market_regime` and `volatility_regime`.

Provenance is diagnostic only. Source names, OCR markers, dataset membership,
PnL, target, MFE, MAE, close time, exit price, exit reason, future returns and
all post-entry fields are prohibited as features.

## Walk-forward and baselines

The Master is evaluated with temporal expanding folds, purging and embargo.
Each fold has fit, validation and test partitions. The `always_allow` baseline
is calculated from exactly the same test trade IDs used by that model fold.
This makes net PnL, profit factor and expectancy comparisons directly paired.

Classifiers:

- Logistic Regression;
- Extra Trees Classifier;
- Random Forest Classifier;
- Histogram Gradient Boosting Classifier.

Expected-net-PnL regressors:

- Huber Regressor;
- Random Forest Regressor;
- Extra Trees Regressor;
- Histogram Gradient Boosting Regressor.

For regressors, `predicted_expected_net_pnl` thresholds are selected only from
the fold validation partition. Paper rows never participate in fitting,
calibration, threshold selection, or candidate ranking.

## Candidate gate

`diagnostic_ranking` orders every evaluated model for analysis.
`eligible_candidate_ranking` contains only models satisfying all gates:

- OOS net PnL exceeds the paired `always_allow` baseline;
- OOS profit factor exceeds the paired baseline;
- OOS expectancy exceeds the paired baseline;
- a strict majority of folds has positive model net PnL;
- block-bootstrap Monte Carlo median net PnL is positive;
- probability of negative Monte Carlo PnL stays below the configured limit;
- no leakage is detected;
- paper usage for fit, calibration and threshold selection is zero.

When no model qualifies, `selected_candidate` is `null` and the decision is
`NO_ELIGIBLE_MODEL_CANDIDATE`. Ranking never grants promotion authority.

## Drift diagnostics

Concept drift is measured across these explicit cohorts:

- Master before `2026-06-10`;
- Master on or after `2026-06-10`;
- OCR V2 tail;
- historical non-OCR rows;
- paper rows through `2026-07-16T17:17:22.249Z`.

The report includes PSI, two-sample KS, Wasserstein distance, label drift and
net-PnL drift, plus decompositions by symbol, side, ISO week and provenance.
Missing cohorts remain visible as controlled diagnostic insufficiency; they are
never synthesized.

The 576 current paper trades are frozen as
`paper_evaluation_set_v1_consumed`. The timestamp
`2026-07-16T17:17:22.249Z` is the prospective watermark. Closed paper trades
after it belong to `prospective_holdout_v2` and are not retroactively folded
into V1.

## CLI

No-write is the default:

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

`--write-research-artifacts` is the only opt-in write path. It is restricted to
ignored research evidence under `data/research` and reports under
`data/reports`. It never writes Master, paper DB, runtime, active registry,
models, signals, risk state or orders.

Qlib is optional and fail-closed. Its absence or incomplete provider setup is a
reported blocker and does not start any runtime service.
