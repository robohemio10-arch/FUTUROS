# Dashboard Runtime Blockers Operator Pack V1

## Objective

The operator pack is an institutional read-only layer built from the existing runtime blockers remediation runbook. It organizes current blockers into domain/severity groups, a manual checklist, a conservative external execution sequence, expected evidence, and explicit closeout criteria.

It does not remediate anything and is never an authorization source.

## Runbook, Operator Pack, And Producer

- The remediation runbook translates blocker reasons into stable remediation rows.
- The operator pack organizes those rows for manual operator review and closeout.
- A producer is the separate external process that materializes evidence.

Neither the Streamlit component nor the operator-pack CLI executes a producer. Producer hints and verification commands are text only and must be reviewed and run manually outside the dashboard.

## Read-Only Contract

The pack:

- consumes only the materialized remediation payload;
- does not call Docker, a command shell, network services, or private exchange APIs;
- does not change runtime, risk, models, datasets, configuration, or active signals;
- does not send notifications or orders;
- does not enable live trading or canary release;
- does not use Streamlit session state as operational truth.

All checklist items have `execution_allowed=false`, `safe_to_execute_from_dashboard=false`, and `execution_location=manual_outside_dashboard`.

## Recommended External Sequence

1. Refresh critical or stale source-health evidence.
2. Refresh stale runtime safety and kill-switch evidence.
3. Rebuild the runtime evidence pack.
4. Rebuild and review the readiness snapshot.
5. Re-audit paper/shadow soak gap accounting.
6. Rebuild dashboard snapshots from materialized evidence.
7. Re-run auditors and readiness checks without releasing live, canary, or orders.

This sequence is guidance only. It is not executed from the dashboard or operator-pack CLI.

## Expected Evidence

Each checklist item identifies its expected artifact. Closeout requires that the artifact be materialized, valid, current when freshness applies, and that the corresponding blocker disappear from its authoritative reason list after dashboard snapshots are rebuilt.

Source-health blockers close only through `global_blocking_reasons`. Runtime-evidence blockers close only through `runtime_evidence_blocking_reasons`. `combined_blocking_reasons` remains a diagnostic union and is not an independent authority.

## Closeout Criteria

Closeout requires all of the following:

- every critical blocker has a completed manual checklist item;
- every expected artifact was independently inspected;
- critical/stale source rows are healthy and current;
- runtime evidence and readiness no longer report their blocker reasons;
- snapshots were rebuilt from materialized evidence;
- the three blocker lists remain semantically separate;
- no safety flag or authorization changed.

Closing the pack does not authorize live trading, canary operation, or order submission.

## Readiness Duration

Seven days of paper/shadow evidence are diagnostic only. Thirty continuous valid days are the minimum readiness requirement. Reaching either duration never performs an automatic release.

## Forbidden Actions

The operator pack must never execute producers, call Docker or network services, use private exchange access, change runtime/risk/model/dataset/config/signals, send Telegram or NTFY, submit orders, or release live/canary operation.

## CLI

Read-only output:

```powershell
python scripts/build_dashboard_runtime_blockers_operator_pack_v1.py --project-root . --json
```

Explicit report materialization:

```powershell
python scripts/build_dashboard_runtime_blockers_operator_pack_v1.py --project-root . --write-report --json
```

Without `--write-report`, nothing is written. With it, the only permitted output is:

`data/reports/dashboard_runtime_blockers_operator_pack_v1.json`

The report is runtime evidence and must not be versioned.
