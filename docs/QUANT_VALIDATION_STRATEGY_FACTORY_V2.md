# B04 — Quantitative Validation and Strategy Factory V2

## Purpose

B04 creates one mandatory, versioned and fail-closed validation protocol for all
strategy families. It consumes trade-level candidate evidence already produced
with the B03 event-driven execution engine and full cost model. It never replaces
B03 execution with a vectorized fill approximation.

## Validation chain

`DatasetAuthority → DataQuality → AntiLeakage → TemporalSplit → B03Execution →
CostReconciliation → WalkForward → CPCV/PBO → MonteCarlo/Bootstrap →
MultipleTesting → ParameterSurface → OOSSegments → Scorecard → ResearchRegistry`

The protocol includes expanding, rolling and anchored temporal splits; purging;
embargo; fold-level IS/validation/OOS metrics; CPCV/PBO; deterministic trade
permutation, IID bootstrap, block bootstrap and cost stress; risk of ruin;
Deflated Sharpe Ratio; White Reality Check; Bonferroni, Holm and
Benjamini-Hochberg adjustments; parameter-neighbourhood stability; and OOS
segmentation by symbol, side, regime, volatility, liquidity and funding.

## Input contract

Every candidate row must contain at least:

- `candidate_id`, `trade_id`, `symbol`, `side`;
- `open_time_utc`, `close_time_utc`;
- `gross_pnl`, `total_cost`, `net_pnl`;
- `execution_engine_version=futures_execution_realism_engine_v2`;
- a non-null `cost_model_hash`.

A shared `observation_id` is recommended so CPCV and White Reality Check can
align candidate outcomes on exactly the same observations. Permanent-quarantine
input is blocked from candidate authority.

## Candidate decisions

Only these terminal decisions exist:

- `REJECTED_DATA_QUALITY`
- `REJECTED_LEAKAGE`
- `REJECTED_INSUFFICIENT_SAMPLE`
- `REJECTED_OVERFIT`
- `REJECTED_UNSTABLE_PARAMETERS`
- `REJECTED_NEGATIVE_OOS`
- `REJECTED_MATERIAL_NEGATIVE_SEGMENT`
- `REJECTED_RISK_OF_RUIN`
- `REJECTED_COST_SENSITIVITY`
- `RESEARCH_CHALLENGER`
- `RESEARCH_BASELINE_CONTROL`

There is no live, canary, active, deployed or promoted decision.

## CLI

Default fixture, no write:

```powershell
python scripts/build_quant_validation_strategy_factory_v2.py `
  --project-root . `
  --no-write `
  --json
```

External B03 candidate evidence:

```powershell
python scripts/build_quant_validation_strategy_factory_v2.py `
  --project-root . `
  --input data/research/b04_candidate_evidence.parquet `
  --config config/quant_validation_strategy_factory_v2.json `
  --no-write `
  --json
```

`--write-report` is optional and restricted to `data/reports`, using the B01
atomic writer. Materialized reports are runtime/research artifacts and must not
be versioned.

## Safety boundary

The package is paper/shadow/research-only. It cannot update Freqtrade,
RiskManager, Qlib runtime, AI Shadow runtime, active signals, active registries,
models, SQLite or Parquet. It cannot access private exchange endpoints or submit
orders. Candidate registry records are content-addressed evidence only and have
no operational authority.
