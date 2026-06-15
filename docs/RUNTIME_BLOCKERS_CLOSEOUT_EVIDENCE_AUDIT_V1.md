# Runtime Blockers Closeout Evidence Audit V1

## Objective

This audit verifies that runtime/dashboard blockers remain open or are closed only through valid materialized evidence. It detects visual bypasses, artificial blocker-list removal, contradictory payload status, invalid required timestamps, and unsafe release flags.

The audit is read-only and is not an operational authorization source.

## Evidence Chain

The audit crosses:

- source health and freshness rows;
- runtime evidence pack;
- readiness snapshot;
- paper/shadow soak gap accounting;
- dashboard build summary and global status snapshot;
- runtime blockers remediation runbook;
- runtime blockers operator pack;
- kill-switch and runtime-safety materialized state.

No producer is executed while collecting or evaluating this evidence.

## Blocker, Runbook, Operator Pack, And Closeout Evidence

- A blocker is an authoritative reason that prevents dashboard/runtime readiness.
- The remediation runbook maps a blocker to manual remediation guidance.
- The operator pack organizes those mappings into an external checklist and closeout criteria.
- Closeout evidence proves that the expected artifact exists, is valid, is current when freshness applies, does not contradict closeout, and caused the blocker to disappear from its authoritative list.

The runbook and operator pack cannot prove closeout by themselves.

## Valid Closeout

Closeout is allowed only when:

- no critical blocker remains;
- the expected evidence is materialized;
- required UTC timestamps are valid;
- payload status does not remain blocked, failed, stale, or missing;
- source health is not blocked;
- blocker lists remain semantically consistent;
- all safety and release flags remain locked;
- no runbook or operator-pack claim contradicts the evidence.

`closeout_allowed=false` while any critical blocker or safety violation remains.

## Bypass Indicators

The audit blocks on indicators including:

- empty `global_blocking_reasons` while source health is `BLOCKED`;
- empty `runtime_evidence_blocking_reasons` while runtime evidence, readiness, or soak remains blocked;
- `combined_blocking_reasons` differing from the union of the two authoritative lists;
- dashboard `OK` with a required source stale, missing, invalid, or malformed;
- unsafe live, canary, order, private-exchange, network, or order-sending flags;
- missing or invalid timestamp for a freshness-required source;
- runbook/operator-pack closeout claims without corresponding valid evidence.

## Readiness Duration

Seven days of paper/shadow evidence are diagnostic only. Thirty continuous valid days are the minimum readiness requirement. Neither threshold automatically releases live trading, canary operation, or order submission.

## Prohibited Actions

The audit must never execute producers, call Docker or a command shell, call network services, use private exchange access, modify runtime/risk/model/dataset/config/signals, send notifications, submit orders, or hide blockers.

## CLI

Read-only audit:

```powershell
python scripts/audit_runtime_blockers_closeout_evidence_v1.py --project-root . --json
```

Explicit report materialization:

```powershell
python scripts/audit_runtime_blockers_closeout_evidence_v1.py --project-root . --write-report --json
```

Without `--write-report`, nothing is written. With it, the only permitted output is:

`data/reports/runtime_blockers_closeout_evidence_audit_v1.json`

The report is runtime evidence and must not be versioned.
