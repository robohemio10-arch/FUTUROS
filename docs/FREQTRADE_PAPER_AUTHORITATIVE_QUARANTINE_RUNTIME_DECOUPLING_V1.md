# Freqtrade Paper Authoritative Quarantine Runtime Decoupling V1

## Objective

This contract separates two independent conclusions produced by the read-only
Freqtrade paper adapter:

1. closure of the fixed historical forensic cohort; and
2. health of all quarantines found in the current paper evidence.

The adapter remains paper/shadow-only, fail-closed, and in-memory. It does not
write the Trader Master, SQLite, runtime state, models, risk configuration, or
orders.

## Architectural cause

The previous closeout condition compared the expected historical remainder
`141/258/561` with the complete current quarantine set. That made an immutable
historical proof depend on a growing runtime observation. New, valid findings
such as `653/669` therefore invalidated the historical closeout even though the
fixed recoveries `221/234` remained correct.

Replacing the old count with a new fixed count would preserve the same defect.
The corrected contract classifies each dimension independently.

## Historical forensic batch

The historical cohort is frozen:

- targets: `141`, `221`, `234`, `258`, `561`;
- only authorized recoveries: `221`, `234`;
- expected historical remainder: `141`, `258`, `561`.

`batch_closeout_status` keeps its original historical meaning for compatibility.
The explicit alias `historical_batch_closeout_status` makes that meaning
auditable. A completed historical closeout requires all target rows to remain
observable, exactly `221/234` to be recovered, and exactly `141/258/561` from
the historical cohort to remain quarantined.

Any missing target, missing recovery, unexpected recovery, or unexpected state
inside the historical cohort blocks the historical closeout. The fixed recovery
allowlist in `quarantine_recovery.py` is unchanged.

## Current runtime quarantine health

`quarantined_order_ids` and `quarantined_row_count` continue to represent every
current finding. `remaining_quarantined_order_ids` remains a compatibility alias
for that complete list.

Current quarantines outside the historical cohort are published in
`additional_runtime_quarantined_order_ids`. The fields
`runtime_quarantine_status` and `runtime_quarantine_reason` classify the current
evidence independently. Any current quarantine keeps runtime health and the
global adapter report blocked.

Consequently, these results can coexist without contradiction:

```text
historical_batch_closeout_status=completed_with_quarantine
runtime_quarantine_status=blocked
status=blocked
```

For the current evidence, `653/669` remain visible additional quarantines. They
are not recovered, accepted, downgraded, or hidden.

## Report fields

The adapter adds:

- `historical_batch_closeout_status` and `historical_batch_closeout_reason`;
- `historical_batch_closeout_complete`;
- historical target, recovered, remaining, missing, and unexpected ID lists;
- `historical_batch_contract_definition_errors`;
- `additional_runtime_quarantined_order_ids`;
- `runtime_quarantine_status` and `runtime_quarantine_reason`.

Existing global quarantine fields and safety flags retain their public meaning.
No downstream consumer needs to infer runtime readiness from the historical
batch status.

## Validation policy

Deterministic fixtures prove the fixed cohort with zero, one, and two additional
runtime quarantines. Negative fixtures prove fail-closed behavior when a target
disappears, an authorized recovery is missing, or an unapproved recovery is
reported. The real-source probe verifies source hashes before and after the
no-write evaluation and treats the current quarantine count as evidence rather
than a historical invariant.

The duplicate full-exit guards for `653/669` and the paper exit lifecycle remain
unchanged and are regression-tested separately.

## Operational boundary

This change authorizes no rollout. It does not modify Strategy, RiskManager,
Freqtrade configuration, Qlib, IA Shadow, Docker, models, datasets, live/canary
controls, exchange access, or order submission. Containers are not restarted.
