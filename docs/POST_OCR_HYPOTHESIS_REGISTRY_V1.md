# POST-OCR Hypothesis Registry V1

## Scope

Branch 07 implements the WQ7 post-OCR economic hypothesis registry.

The registry is research/paper/shadow only. It has no operational authority and does not train models, evaluate candidates, change RiskManager, modify Freqtrade, update operational thresholds, access private exchange APIs, send orders, or promote models.

## Frozen lineage

The implementation consumes only the previously frozen evidence chain:

- WQ2 temporal walk-forward evidence;
- WQ3 segment-persistence evidence;
- WQ4 regime/concentration evidence;
- WQ5 Monte Carlo/risk evidence;
- WQ6 PIT Qlib dataset evidence;
- WQ7 method freeze V1;
- WQ7 deterministic candidate-queue policy V1;
- WQ7 segment-value semantic normalization policy V1;
- WQ7 candidate queue V2.

Canonical WQ7 method hash:

81cb14553b054a90eca2597073b20c4902cd1672759403ef0b4779eab9707d33

Canonical Queue V2 hash:

3ceaa95ea1f262dcf289654e3ed66efd1f0df47764db88fbd3d39660a1f0f956

Portable registry hash:

79195333de12e1925302082246e3133e2b5d26f4bd4f88250504b10107bc04b4

The registry hash excludes only local locator fields method_freeze.path and source_queue.path. Artifact hashes, method hashes, candidate definitions, metrics, gates, fingerprints, state and authority remain inside the semantic fingerprint.

## Registered hypotheses

The V1 registry contains exactly two hypotheses.

### H01 - Qlib ranking challenger

ID:

WQ7-H01-qlib_ranking_challenger-fa9ea5e088e668e5

State: HOLD

The exact model family and hyperparameter envelope have not yet been frozen. No training or threshold evaluation is authorized by WQ7.

Dataset fingerprint:

fa9ea5e088e668e5f733cde841da849dc3c16263a672991f1803e609fff14a25

Split-manifest fingerprint:

248d53e2481cf107f88a4f7267b9573f118d3fed1afef7906d290e741b3229bc

### H02 - Entry-context segment filter

ID:

WQ7-H02-segment_filter-segment_99f7fa60c4edfd02ea462561

State: HOLD

Selected WQ3 segment:

segment_99f7fa60c4edfd02ea462561

Family: open_hour_utc

Historical raw WQ3 representation: (23,)

Canonical semantic value: 23

Matching mode: exact_semantic_equality

No fuzzy, nearest or backfill matching is allowed.

The WQ3 discovery population cannot be reused for acceptance. Independent OOS evidence is mandatory before any state transition.

## Empty hypothesis slots

Slot 3, regime abstention, remains empty because all nine supported WQ4 regimes were positive under the frozen eligibility rule. The slot cannot be refilled post hoc.

Slot 4, exit/time-stop/ATR research, remains empty because the frozen WQ2-WQ6 evidence set does not establish an independently auditable exact intrabar reconstruction artifact. The slot cannot be refilled post hoc.

## WQ5 inheritance

WQ5 engineering validation passed, but quantitative acceptance remains failed under the frozen stress methodology.

Classification inherited:

DISCARD_OR_RECALIBRATE_RESEARCH_ONLY

No candidate may claim robustness from historical unstressed OOS evidence alone.

## Challenger consumption contract

Consumers must reference the immutable hypothesis_id and registry hash.

Consumers must preserve the dataset and split fingerprints associated with the hypothesis.

A HOLD state does not authorize training, model selection, operational activation, threshold mutation or state transition.

A future state transition requires separate evidence under the pre-registered acceptance gate.

A surviving candidate requires a separate pre-registered Paper A/B package.

The CLI consumes the registry through the same public consumer API used by future challengers and reports the registered hypothesis IDs without evaluating them.

## CLI

No-write is the default.

Example:

python scripts/build_post_ocr_hypothesis_registry_v1.py --method-freeze <WQ7_METHOD_FREEZE_V1.json> --queue-v2 <WQ7_HYPOTHESIS_CANDIDATE_QUEUE_V2.json> --json

Persistence requires both --write and --output.

Repository runtime/data/report/log/Freqtrade scopes are rejected as registry output destinations.

## Safety invariants

- paper_only=true
- shadow_only=true
- research_only=true
- operational_authority=false
- sends_orders=false
- changes_risk=false
- changes_model=false
- exchange_private_access=false
- live_trading_enabled=false
- canary_enabled=false
- training_performed=false
- candidate_evaluation_performed=false
- independent_oos_evaluation_performed=false
- threshold_search_performed=false
- model_selection_performed=false

## Branch boundary

This branch creates and exposes the hypothesis registry. It does not prove an economic edge.

The two registered hypotheses remain HOLD.

Subsequent challenger/model branches must consume the immutable registry identity and supply the independent evidence required for any transition.

## Real Qlib challenger consumption

The registry is consumed by the existing institutional Qlib ranking challenger:

scripts/train_qlib_institutional_ranking_challenger_v1.py

smartcrypto/learning/qlib_trainer/ranking_trainer.py

The consumer accepts an optional WQ7 binding composed of hypothesis_registry_path and hypothesis_id.

Canonical consumed hypothesis:

WQ7-H01-qlib_ranking_challenger-fa9ea5e088e668e5

Canonical portable registry hash:

79195333de12e1925302082246e3133e2b5d26f4bd4f88250504b10107bc04b4

The binding validates the actual WQ6 dataset_hash and split_manifest_hash against the immutable H01 fingerprints before reporting registry consumption.

The current H01 state is HOLD.

Registry-bound dry-run is permitted for lineage and integration evidence.

Registry-bound training while H01 is HOLD is fail-closed.

A HOLD binding does not authorize model fitting, threshold search, candidate evaluation, model promotion, registry mutation, runtime changes, RiskManager changes, Freqtrade changes, private exchange access, canary release, live release or order submission.

Legacy Qlib trainer invocations without WQ7 binding remain backward-compatible.

The real-consumer integration was validated against the exact WQ6 materialized dataset with:

- dataset fingerprint match: true
- split-manifest fingerprint match: true
- dry-run registry consumption: pass
- HOLD training block: pass
- challenger training performed: false
- candidate evaluation performed: false
- model promotion performed: false
- registry write performed: false
