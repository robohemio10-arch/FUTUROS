# Market Features Rematerialization and First Training Runs V1

## Boundary

This package rematerializes point-in-time five-minute features and evaluates
ephemeral research challengers. It is paper/shadow/research-only. It does not
serialize or promote models, update a registry, change runtime or risk, submit
orders, or access a private exchange.

Financial execution is fail-closed to the exact canonical environment:

| Component | Required version |
| --- | --- |
| Python | 3.11.15 |
| scikit-learn | 1.8.0 |
| joblib | 1.5.3 |

Other environments may diagnose data, alignment, lineage and drift. They may
not run baselines, fitting, threshold selection, backtests, Qlib training,
paper model evaluation or Monte Carlo.

## Point-in-time materialization

Canonical sources are `data/trades/trades_master.parquet`,
`data/features/market_features_60d.parquet` and the query-only paper snapshot.
Paper access requires `--allow-paper-read`.

```text
available_at_utc = candle_timestamp_utc + 5 minutes
available_at_utc <= trade_open_time_utc
0 <= feature_age_seconds < 300
```

Only completed candles are joined. Gaps are never crossed by forward fill and
missing values are never imputed. One-minute data remains blocked for the
confirmed period of zero paper coverage. The historical 3,504-row expectation
is reconciled explicitly with all 3,562 canonical Master rows; no row is
silently removed.

## Feature boundary

The model matrix contains contemporaneous OHLCV indicators and deterministic
representations of symbol, side, entry hour, weekday, feature age, market
regime and volatility regime. Provenance, dataset membership and cohort labels
are diagnostics only. PnL, targets, MFE, MAE, close time, exit fields, future
returns and every post-entry value are prohibited.

Paper rows never enter fitting, calibration, threshold selection, ablation or
candidate selection. No row is excluded based on outcome or to manufacture a
positive result. Only the pre-existing point-in-time row validation controls
model eligibility.

## Models and thresholds

Classifiers are Logistic Regression, Extra Trees, Random Forest and Histogram
Gradient Boosting. Expected-net-PnL regressors are Huber, Random Forest, Extra
Trees and Histogram Gradient Boosting.

Walk-forward folds are expanding, purged and embargoed. Regressor thresholds
are selected exclusively inside each Master fit/validation interval. The paper
set cannot influence a threshold.

## Fold-matched always-allow baseline

Every candidate is compared with `always_allow` on the exact same temporal OOS
trade IDs. Fold sequences are concatenated in chronological fold order before
aggregate metrics are calculated. The aggregate publishes:

- `trade_count` and `active_trade_count`;
- `gross_profit`, `gross_loss` and `net_pnl`;
- `expectancy` and `profit_factor`;
- maximum drawdown over the concatenated temporal sequence;
- `fold_count`.

The following invariants are enforced without repair:

```text
net_pnl == gross_profit - gross_loss
expectancy == net_pnl / trade_count
trade_count > 0 when net_pnl != 0
profit_factor is null if and only if gross_loss == 0
```

## Typed concept drift

Continuous features use quantile PSI, two-sample KS and Wasserstein distance.
A degenerate reference that cannot define quantile bins reports PSI as null;
the implementation never substitutes an artificial fixed value.

Binary features use reference/target prevalence, categorical PSI and
Jensen-Shannon divergence. Categorical fields use explicit distributions,
Jensen-Shannon, categorical PSI and chi-square only when expected cell counts
are valid. Label and net-PnL drift remain separate outcome diagnostics.

Comparisons cover Master before/after `2026-06-10`, OCR V2 tail, historical
pre-V2, historical non-OCR and paper through
`2026-07-16T17:17:22.249Z`. Decomposition is by symbol, side, ISO week and
provenance. Provenance never becomes a feature.

## Cohort-aware experiments

The research report contains six non-authoritative experiments:

| ID | Contract |
| --- | --- |
| E1 | full Master population, purged walk-forward |
| E2 | historical pre-V2 population |
| E3 | OCR V2 tail population |
| E4 | train historical pre-V2, test OCR V2 tail |
| E5 | train before 2026-06-10, test on/after the cutoff |
| E6 | full-population OOS attribution by cohort |

E2/E3 use their own temporal walk-forward where sample size permits. E4/E5
purge training rows against the first test timestamp and reserve a Master-only
validation interval. Every experiment uses the always-allow baseline on its
exact test rows. Insufficient cohorts remain blocked and visible.

E1 is the only source considered by the research candidate gate. E2-E6 are
diagnostic and cannot grant eligibility.

## Fold 3 attribution

Fold 3 OOS contribution is reported for each model by provenance, ISO week,
symbol, side, pre/post-cutoff period and OCR V2 tail versus historical pre-V2.
Each cell includes candidate and always-allow metrics on identical rows and the
net-PnL delta. Paper rows are absent.

## Candidate gate and paper watermark

`diagnostic_ranking` contains every evaluated E1 model.
`eligible_candidate_ranking` requires better OOS net PnL, profit factor and
expectancy than fold-matched always-allow, a majority of positive folds,
positive Monte Carlo median, acceptable negative-PnL probability, no leakage
and zero paper use. Otherwise `selected_candidate` is null and the decision is
`NO_ELIGIBLE_MODEL_CANDIDATE`.

The 576 current paper trades are frozen as
`paper_evaluation_set_v1_consumed`. Trades after
`2026-07-16T17:17:22.249Z` form `prospective_holdout_v2` and never retroactively
change V1 fitting or thresholds.

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

`--write-research-artifacts` is the only write opt-in and is restricted to
ignored `data/research` evidence and `data/reports`. It never writes Master,
paper DB, runtime, active registry, model, signal, risk state or orders. Qlib is
optional and fail-closed.
