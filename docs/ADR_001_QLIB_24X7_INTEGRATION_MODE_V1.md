# ADR-001: Qlib 24/7 Integration Mode V1

## Status

Approved for research architecture only. The selected mode has no operational authority and does not authorize training, model promotion, runtime updates, private exchange access, or order submission.

## Context

SMART FUTUROS operates on continuous cryptocurrency markets. Its canonical learning lineage already uses explicit UTC timestamps, versioned Parquet datasets, a deterministic FeatureContract, a DatasetManifest, separated financial labels, and purged walk-forward evidence. Qlib is an optional research backend, not an operational data authority.

This ADR decides how future research code may connect those governed artifacts to Qlib without importing assumptions from exchange-session calendars or coupling production behavior to a Qlib provider.

## Problem

Native Qlib workflows commonly assume a provider URI and a trading calendar. An unqualified provider adaptation can introduce artificial market closures, silent timezone conversion, divergent timestamp ordering, or different behavior across Windows, Linux, and Docker. Any of those effects would weaken lineage and anti-leakage guarantees.

The project must choose between:

- **Mode A - model zoo over governed Parquet:** Qlib estimators consume project-owned, versioned Parquet through a research adapter.
- **Mode B - native provider/calendar:** project data is adapted to a Qlib-native provider and continuous calendar.

## Existing Capabilities Reused

The ADR adds no parallel training or dataset subsystem. It relies on:

- `smartcrypto/learning/qlib_backend_environment_lock`: dependency pinning, importability audit, and research isolation.
- `smartcrypto/learning/qlib_backend_gate`: dependency contract, backend probe, and runtime isolation.
- `smartcrypto/learning/feature_contracts`: deterministic FeatureContract and DatasetManifest with source hashes and anti-leakage roles.
- `smartcrypto/learning/target_store`: separated financial labels and triple-barrier-compatible target schema.
- `smartcrypto/learning/walkforward`: purged temporal splits, embargo, baselines, and leakage audit.
- `smartcrypto/learning/qlib_trainer`: dataset adapter, challenger trainer, walk-forward evaluator, and research artifacts without promotion authority.
- `smartcrypto/ml/model_registry.py`: champion/challenger gate that does not auto-promote.
- `smartcrypto/ml/model_decision_logger.py` and `smartcrypto/ml/outcome_tracker.py`: Decision/Outcome evidence separated from feature production.

The machine-readable validation is integrated into `qlib_backend_environment_lock`; it does not import or initialize Qlib.

## Decision Drivers

1. Continuous crypto calendar without synthetic sessions.
2. Deterministic UTC timestamps and ordering.
3. Preservation of DatasetManifest and FeatureContract lineage.
4. Labels remain separate from features and unavailable at decision time.
5. Purging, embargo, CPCV/PBO evidence, and cost assumptions remain project-owned.
6. Equivalent research behavior on Windows, Linux, and Docker.
7. No Qlib-provider authority over operational runtime.
8. Portable rollback without dataset conversion.

## Comparative Matrix

| Criterion | Mode A: model zoo + versioned Parquet | Mode B: native provider/calendar |
| --- | --- | --- |
| Real 24/7 support | Explicit timestamps preserve every continuous interval | Must prove native continuous-calendar behavior without artificial sessions |
| Temporal determinism | DatasetManifest hash and ordered UTC columns are project-owned | Depends on provider ingestion, calendar generation, and Qlib version |
| Timezone | UTC is explicit before adapter entry | Must prove no localization or silent conversion |
| Reproducibility | Versioned files and hashes are stable inputs | Requires provider-state snapshot and deterministic rebuild evidence |
| DatasetManifest | Preserved directly | Must prove provider materialization preserves manifest lineage |
| FeatureContract | Adapter consumes deterministic feature order | Must prove provider expressions do not alter roles or ordering |
| Labels | Separate TargetStore contract | Must prove labels remain separated from provider features |
| Purging and embargo | Existing project split engine remains authoritative | Calendar interactions require equivalence evidence |
| CPCV/PBO | Future evidence remains project-controlled | Requires provider-calendar equivalence before use |
| Costs | Explicit cost-model contract required | Must prove equivalent fee, funding, and slippage inputs |
| Windows/Linux/Docker | Parquet transport is portable and already governed | Native provider persistence needs cross-platform proof |
| Qlib coupling | Limited to research adapter and estimator API | High coupling to provider layout and calendar behavior |
| Runtime isolation | Provider has no runtime authority | Must prove provider remains research-only and removable |
| Failure recovery | Reopen immutable dataset and manifest | Rebuild provider state and verify hashes/calendar |
| Observability | Source hashes, schema, time bounds, and split reports are explicit | Additional provider-state and calendar observability required |
| Operational complexity | Low; no provider service or operational cache | Higher; provider lifecycle and compatibility must be governed |
| Leakage risk | Existing FeatureContract and point-in-time splits remain intact | Calendar/provider transforms add an unproven leakage surface |
| Rollback | Stop using adapter; governed inputs remain unchanged | Must export/reconcile provider state back to governed artifacts |

## Decision

Select **Mode A: `model_zoo_versioned_parquet`**.

- Calendar: `continuous_crypto_24x7_utc`.
- Timezone: `UTC`.
- Dataset transport: `versioned_parquet`.
- Qlib provider runtime authority: `false`.
- Architectural rollback mode: Mode A itself; no provider migration is required.

Mode B remains an unapproved alternative. It can only be reconsidered when reproducible evidence satisfies every Mode B gate in the validator, including 24/7 behavior, timestamp determinism, manifest/contract preservation, cross-platform equivalence, runtime independence, and anti-leakage equivalence.

## Calendar Policy

The canonical calendar is continuous UTC time. Weekends, holidays, and exchange-session boundaries do not close the calendar. Missing observations are data-quality facts and must not be converted into synthetic closures or silently forward-filled. Frequency alignment must preserve the source timestamp and its availability at decision time.

## Timezone Policy

All persisted and in-memory timestamps entering a future Qlib adapter must be timezone-aware UTC or explicitly normalized to UTC under a tested source contract. Naive timestamps, local-time defaults, daylight-saving conversion, and silent timezone coercion are blocked.

## Contracts

### FeatureContract

Only columns classified as features may enter a model. `future_ret_*`, `target_*`, labels, outcomes, and post-trade fields remain forbidden as model features. Feature order and dtypes must match the versioned contract.

### DatasetManifest

Every future training candidate must carry source hashes, dataset hash, row/column counts, UTC bounds, symbols, sides, label distribution, and the FeatureContract hash. A Qlib adapter may consume this evidence but cannot replace it.

### Labels and Costs

Labels must come from the separated TargetStore/label contract. A versioned cost model covering applicable fees, funding, and slippage is mandatory before future evaluation. Neither labels nor costs are inferred by this ADR.

### Anti-Leakage

Future training requires point-in-time feature availability, strict temporal splits, purging, embargo, and documented CPCV/PBO evidence where applicable. Perfect metrics are not accepted without methodological explanation.

## Research and Runtime Isolation

Qlib remains a research backend. This ADR does not update `smartcrypto/qlib_engine`, active models, registries, signal producers, Freqtrade, RiskManager, Docker, or runtime data. The validator reads one versioned JSON file and returns an in-memory report. It never initializes Qlib or loads datasets/models.

## Gates for Future Training Authorization

This ADR does **not** authorize training. A future branch must independently prove:

1. Qlib dependency lock and backend compatibility are valid.
2. DatasetManifest, FeatureContract, label contract, and cost model are current and mutually consistent.
3. Calendar and timezone policies pass deterministic fixtures on Windows, Linux, and Docker.
4. Purged walk-forward and embargo evidence pass with no leakage.
5. CPCV/PBO and baseline comparisons are documented when applicable.
6. Challenger-only output paths are isolated from active registry/runtime.
7. Training is explicitly requested and remains paper/shadow-only.

Promotion requires a separate governance decision and remains forbidden here.

## Consequences

### Positive

- Existing lineage and anti-leakage contracts stay authoritative.
- Qlib can be replaced without migrating operational data.
- Cross-platform research remains inspectable through immutable artifacts.
- Failure recovery is file- and hash-based.

### Negative

- Some Qlib-native provider conveniences are intentionally unavailable.
- The project must maintain a narrow dataset adapter.
- Provider-specific alpha expressions require explicit translation and validation.

## Risks and Mitigations

- **Calendar mismatch:** exact calendar constant and UTC validation block session-based contracts.
- **Silent feature drift:** FeatureContract and DatasetManifest remain mandatory gates.
- **Research-to-runtime leakage:** all runtime, promotion, model, risk, and order authorities are false and validated.
- **Mode B selected without proof:** the validator requires all Mode B evidence gates before accepting that mode.

## Architectural Rollback

Mode A does not mutate source datasets, so rollback means removing or disabling a future research adapter while retaining the same Parquet and manifests. If Mode B is ever evaluated, its rollback target is Mode A and no provider state may become the sole authoritative copy.

## Compatibility

- **Windows:** paths are resolved with `pathlib`; the contract contains no provider database path.
- **Linux:** JSON and Parquet contracts are platform-neutral.
- **Docker:** no service, volume, provider cache, or image change is introduced.

Cross-platform claims apply to the architectural contract. Any future trainer still requires dedicated reproducibility evidence.

## Out of Scope

- Qlib or IA Shadow training.
- Challenger creation or model promotion.
- Model registry or active-model writes.
- Qlib/IA Shadow runtime changes.
- Freqtrade, RiskManager, signal, order, live, or canary changes.
- Backfill, feedback-gap remediation, microbatch, autotrain, or scheduler execution.
- Provider URI creation or native calendar implementation.
- Dataset, Parquet, SQLite, Docker, `.env`, or `data/**` changes.
