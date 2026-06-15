# Runtime Evidence Freshness Remediation Producers Audit V1

## Objective

This institutional read-only audit identifies which documented external producers must be run manually to refresh critical stale runtime/dashboard evidence. It never executes a producer and never authorizes operational release.

## Current External Producers

The current critical freshness chain maps to:

- market data health: `scripts/run_market_data_health_audit.py`, producing `data/reports/market_data_health_audit_report.json`;
- kill switch: `scripts/set_kill_switch.py`, preserving `enabled=true` while refreshing `data/runtime/kill_switch.json` after manual review;
- runtime safety config: `scripts/validate_runtime_safety_config.py`, validating `config/paper.example.yml` into `data/runtime/runtime_safety_audit_config.json`.

The commands are rendered as text only. Operators must review inputs and execute them outside Streamlit and outside the audit CLI.

## Audit, Producer, And Dashboard

- The audit reads the materialized source health matrix and maps stale blockers to documented producers.
- The producer is the separate project routine that materializes refreshed evidence.
- The dashboard presents the audit payload and never calls the producer.

Refreshing a timestamp alone does not prove readiness. The resulting payload must remain valid, safe, and semantically consistent.

## Post-Execution Verification

After each manual producer run:

1. Inspect the expected JSON artifact.
2. Verify a valid current UTC timestamp.
3. Confirm safety flags remain paper/shadow locked.
4. Confirm the kill switch remains enabled during this blocked state.
5. Rebuild dashboard snapshots externally.
6. Verify the refreshed source ID disappeared from `global_blocking_reasons` because source health became valid, not because the list was edited.
7. Re-run closeout, operator-pack, runbook, and readiness audits.

## Closeout Criteria

A freshness blocker closes only when its source is materialized, valid, current, healthy, and absent from the authoritative source-health blocker list after snapshot rebuild.

The audit returns:

- `warning` when external producers are required and fully mapped;
- `ok` when no critical freshness blocker remains;
- `blocked` when a critical blocker has no mapping, a critical timestamp is invalid, or safety is inconsistent.

## Readiness Duration

Seven days of paper/shadow evidence remain diagnostic only. Thirty continuous valid days remain the minimum readiness requirement. Refreshed evidence never automatically enables live trading, canary operation, or order submission.

## Prohibited Actions

The audit and dashboard must never execute producers, call Docker or subprocesses, access network/private exchange APIs, disable the kill switch, modify risk/models/datasets/config/signals, send notifications/orders, hide blockers, or release live/canary operation.

## CLI

Read-only audit:

```powershell
python scripts/audit_runtime_evidence_freshness_remediation_producers_v1.py --project-root . --json
```

Explicit report materialization:

```powershell
python scripts/audit_runtime_evidence_freshness_remediation_producers_v1.py --project-root . --write-report --json
```

Without `--write-report`, nothing is written. With it, the only permitted output is:

`data/reports/runtime_evidence_freshness_remediation_producers_audit_v1.json`

The report is runtime evidence and must not be versioned.
