# CANONICAL TREATMENT — SCORER COVERAGE DIAGNOSTICS V1

## Scope

This change is diagnostic-only for the Qlib V3 natural evidence producer used by
Canonical Treatment Paper B. It does not alter Paper publication, RiskManager,
order routing, model selection, strategy behavior, live/canary state, or historical
evidence.

## Runtime finding

Seven prospective Control decisions were published while Qlib V3 natural evidence
was not persisted. The seven events were all observed with:

- `phase13_status=ok`
- `publication_status=baseline_preserved`
- `published_signal_count>0`
- `v3_status=blocked`
- reported `v3_reason=decision_model_mismatch`
- `v3_write_performed=false`

The active V3 evidence store does not contain those seven signal occurrences.
They remain historical unmatched observations and MUST NOT be backfilled, rescored,
or reclassified as prospectively treated.

## Root observability defect

`natural_producer.observe_signal_batch()` previously replaced the real shadow
scorer blocker with `decision_model_mismatch` whenever the operational Paper model
hash differed from the frozen V3 model hash.

That loses the actual reason emitted by
`economic_shadow_decision_producer.resolve_shadow_decision_batch()`.

Operational-model mismatch is not itself sufficient to prove a V3 scorer failure.
The V3 shadow scorer is designed to project a separate frozen V3 decision when the
operational decision is not already a certified V3 decision.

## Change

`ProducerReport` gains additive diagnostic fields:

- `operational_model_mismatch_observed`
- `operational_model_mismatch_count`
- `shadow_block_reason`

For a blocked shadow scorer:

- `reason` preserves the exact `shadow.report.reason`;
- `shadow_block_reason` preserves the same underlying blocker;
- operational model mismatch is reported separately and never substitutes for the
  blocker reason.

For successful shadow scoring, operational model mismatch remains observable but
does not alter status, persistence, crosswalk semantics, or Paper publication.

The existing schema version remains unchanged because the change is additive and
does not alter existing field semantics other than correcting the previously
masked diagnostic `reason`.

## Safety

Invariant behavior remains:

- paper/shadow/research only;
- `operational_authority=false`;
- `paper_behavior_changed=false`;
- `sends_orders=false`;
- no historical backfill;
- no V2 evidence import;
- no RiskManager changes;
- no strategy changes;
- no live/canary enablement;
- no order-submission enablement;
- no private exchange access.

## Acceptance

Focused tests must prove:

1. a blocked V3 scorer preserves its real blocker even with an operational model mismatch;
2. a blocked V3 scorer without model mismatch reports mismatch telemetry as false/zero;
3. a successful V3 shadow projection with operational model mismatch persists the exact
   operational-to-V3 crosswalk and remains `status=ok`;
4. new telemetry fields default safely and do not change the existing safety contract.

No historical unmatched row may be modified as part of this branch.


## Runtime-parity contract migration

The pre-existing runtime-parity test pinned the masked `decision_model_mismatch`
diagnostic and the previous `natural_producer.py` source hash. This branch
intentionally replaces those expectations. The parity contract now pins the
complete diagnostic-hardened source and verifies the real shadow blocker plus
separate operational-model-mismatch telemetry.

This is an observability-contract hardening only. It does not change Paper
publication order, RiskManager authority, scoring thresholds, model identity,
historical evidence, or order behavior.
