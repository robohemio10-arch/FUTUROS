# Paper A/B Edge Selector V1

## Status

**Research-only / Paper-only / Shadow-only.** This component has no operational authority. It does not send orders, change risk, change strategy, update active signals, update model registry, promote models, access private exchange APIs, release canary, or release live trading.

Canonical branch: `codex/paper-ab-edge-selector-v1`
Canonical base: `d9c954a56cae0d55d3823dde0ed2810ab5264af3`

## Objective

Build a deterministic, point-in-time and auditable A/B research harness to answer one question:

> Does a financially eligible candidate selector add net incremental edge over the observed Paper baseline?

The branch deliberately separates:

- `software_dod`
- `financial_evidence`
- `treatment_release`

`software_dod=PASS` does **not** imply financial edge. Financial edge does **not** imply release. `treatment_release_allowed` is always `false` in V1.

## Canonical current evidence — 20/08/2026

The pre-implementation probe showed:

- Paper rows: 726
- Closed: 724
- Open: 2
- Net PnL: -77.01329972 USDT
- Profit Factor: 0.77061746
- Expectancy: -0.10637196 USDT/trade
- Max Drawdown: 78.53119224 USDT
- Financial AI OOS rows: 216
- AUC: 0.51248285
- Brier: 0.24051328
- ECE: 0.08704192
- candidate-linked rows: 0
- trusted estimates: 0
- candidate EV generated: 0
- candidate EV blocked: 216
- `candidate_ev_ready=false`
- `drift_gate=false`
- `qlib_lineage_gate=false`
- `trader_master_linkage_gate=false`
- Qlib dependency security: `BLOCKED / upstream_constraint_blocked`

Therefore the correct current V1 outcome is expected to be:

```text
software_dod=PASS
financial_evidence.status=EVIDENCE_BLOCKED
candidate_linked_rows=0
eligible_treatment_count=0
treatment_evaluable=false
decision=MANTER_BASELINE
treatment_release_allowed=false
```

If current sources produce a positive Treatment edge, treat it as a methodological P0 until candidate linkage and all eligibility gates are proven.

## Source authority

Paper outcome authority is the authoritative Freqtrade Paper SQLite snapshot read through the existing `paper_edge_foundation` integrity contract.

Closed-trade PnL authority:

```text
FREQTRADE_CLOSE_PROFIT_ABS
```

Open trades never generate final financial outcomes.

The implementation reuses:

- `read_authoritative_paper_source`
- `prepare_closed_trades`
- `compute_financial_metrics`
- `FinancialAIResearchEngine`
- `AtomicWritePolicy` / atomic writers

It does not create a parallel financial source of truth.

## Deterministic assignment

An eligible candidate is allocated with:

```text
material = experiment_id + "|" + candidate_id
digest   = SHA256(material)
```

The first digest byte defines a 50/50 arm:

- `< 128` -> `CONTROL`
- `>= 128` -> `TREATMENT`

The assignment never uses:

- PnL
- close time
- exit reason
- MFE / MAE
- future return
- runtime execution timestamp

Python `hash()` and global PRNG state are not used.

`trade_id` may be used only to link a Financial AI estimate to the authoritative closed-trade outcome. It is never synthesized into `candidate_id`.

## Treatment eligibility

A candidate is assignable only when all conditions are true:

- `candidate_id` present
- `candidate_linkage_status == LINKED`
- `point_in_time_consumable == true`
- `branch2_compatible == true`
- `financial_estimate_trusted == true`
- `candidate_ev` finite
- `candidate_ev_status == AVAILABLE`
- `candidate_ev_ready == true`
- `regression_quality_gate == true`
- `classification_quality_gate == true`
- `calibration_gate == true`
- `monotonicity_gate == true`
- `drift_gate == true`
- `qlib_lineage_gate == true`
- `trader_master_linkage_gate == true`
- Qlib dependency security materially proves an approved clean resolution

Missing, unknown or blocked security evidence fails closed.

## Offline A/B replay semantics

V1 is an `OFFLINE_POINT_IN_TIME_AB_REPLAY`, not an online causal experiment.

Control action:

```text
BASELINE_ACCEPT_OBSERVED_PAPER_TRADE
```

Treatment action:

```text
candidate_ev > 0 -> ACCEPT
candidate_ev <= 0 -> REJECT
```

For a valid point-in-time Treatment rejection, effective replay PnL is zero. This is an offline no-trade counterfactual for research only. It does not model replacement opportunity, capital redeployment or execution impact unless independently evidenced.

The report explicitly sets:

```text
causal_claim_allowed=false
```

## Quantitative evidence

Per arm:

- trade count
- eligible count
- accepted count
- rejected count
- net PnL
- expectancy
- Profit Factor
- win rate
- payoff ratio
- max drawdown

When directly supported:

- capital-hours
- time-in-market
- fees

Not inferred without evidence:

- spread cost
- slippage
- latency

Primary deltas:

- `delta_net_pnl`
- `delta_expectancy`
- `delta_profit_factor`
- `delta_max_drawdown`

## Bootstrap

The primary statistical gate is a deterministic confidence interval for Treatment minus Control expectancy.

Preferred method:

```text
temporal_cluster_day_bootstrap
```

UTC days are sampled as clusters to reduce the false independence assumption for temporally adjacent trades.

If fewer than two UTC days exist, the implementation reports an explicit fallback:

```text
arm_stratified_iid_bootstrap_fallback
```

Incremental edge is research-supported only when:

- minimum observations per arm are satisfied;
- minimum observation period is satisfied;
- Treatment Profit Factor >= configured minimum (default 1.10);
- lower CI bound for `delta_expectancy` is strictly greater than zero.

Even then the state is:

```text
INCREMENTAL_EDGE_RESEARCH_ONLY
```

and release remains blocked.

## Financial evidence states

Allowed states:

- `EVIDENCE_BLOCKED`
- `INSUFFICIENT_SAMPLE`
- `NO_INCREMENTAL_EDGE`
- `PROMISING_NOT_PROVEN`
- `INCREMENTAL_EDGE_RESEARCH_ONLY`

Top-level decision remains:

```text
MANTER_BASELINE
```

V1 does not promote Treatment into Paper execution.

## Persistence

Default mode is no-write.

Explicitly permitted destinations:

```text
data/reports/paper_ab_edge_selector_v1.json
data/reports/paper_ab_edge_selector_assignments_v1.jsonl
```

The JSONL stores pre-outcome assignment records only. It is idempotent by `assignment_id`. A semantic conflict for the same ID raises an error.

Writes outside `data/reports` are blocked.

## Safety invariants

The report always preserves:

```text
paper_only=true
shadow_only=true
research_only=true
read_only=true
operational_authority=false
writes_sqlite=false
writes_runtime=false
writes_active_signals=false
writes_active_model=false
writes_active_registry=false
trains_active_model=false
promotes_model=false
updates_qlib_runtime=false
updates_ai_shadow_runtime=false
changes_strategy=false
changes_risk=false
changes_stake=false
changes_leverage=false
changes_max_open_trades=false
sends_orders=false
real_order_submission_enabled=false
exchange_private_access=false
live_release_allowed=false
canary_release_allowed=false
treatment_release_allowed=false
```

## Canonical no-write probe

```powershell
python scripts/build_paper_ab_edge_selector_v1.py `
  --project-root . `
  --paper-db data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite `
  --feature-source data/features/market_features_60d.parquet `
  --qlib-source data/predictions/latest_qlib_predictions.parquet `
  --trader-master-source data/trades/trades_master.parquet `
  --experiment-id paper-ab-edge-selector-v1 `
  --no-write `
  --json
```

## Validation

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests/test_paper_ab_edge_selector_v1.py -q
python scripts/generate_project_manifest.py --check
python scripts/scan_versioned_secrets.py --project-root . --json
git diff --check
git status -sb
git status --short
git status --short -- data
```

Do not commit or push before reviewing the real probe.
