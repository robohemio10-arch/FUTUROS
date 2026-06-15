# Dashboard Runtime Blockers Remediation Runbook V1

## Purpose

This runbook is an institutional, read-only view of the blockers already reported by dashboard source health and runtime evidence snapshots. It translates blocker identifiers into operator-facing remediation guidance without executing any remediation.

The payload is exposed as `runtime_blockers_remediation` in the global status, build summary, infrastructure, and active controls snapshots.

## Safety Boundary

The runbook and its Streamlit component:

- read only materialized JSON evidence;
- never execute producers;
- never call network or private exchange APIs;
- never submit, cancel, or amend orders;
- never change risk, models, active signals, datasets, or YAML configuration;
- never send Telegram, NTFY, or other notifications;
- never authorize live or canary release.

`paper_only=true`, `shadow_only=true`, `dashboard_readonly=true`, and all live, order, risk, model, notification, private exchange, and network capabilities remain false.

## Operator Workflow

1. Read `global_blocking_reasons` as the source health/source closeout authority.
2. Read `runtime_evidence_blocking_reasons` separately as the runtime evidence authority.
3. Use `combined_blocking_reasons` only as a diagnostic union.
4. Review every `blocker_row`, including its canonical path, freshness, health, producer hint, and runbook hint.
5. Execute the documented producer manually outside the dashboard only after independent operator review.
6. Inspect the producer output before rebuilding dashboard snapshots.
7. Rebuild snapshots and confirm that blocker changes came from new materialized evidence.

The dashboard must not provide a button, command bus action, or session-state shortcut for these steps.

## Readiness Interpretation

Seven days of paper/shadow evidence are diagnostic. They can reveal continuity and data-quality problems but are not sufficient for readiness.

Thirty continuous valid days are the minimum readiness requirement. Reaching that duration does not automatically release live trading, canary operation, or order submission. Those capabilities remain blocked until the separate governance and authorization process is completed.

## CLI Audit

Read-only audit:

```powershell
python scripts/audit_dashboard_runtime_blockers_remediation_runbook_v1.py --project-root . --json
```

Explicit report materialization:

```powershell
python scripts/audit_dashboard_runtime_blockers_remediation_runbook_v1.py --project-root . --write-report --json
```

Without `--write-report`, the CLI writes nothing. With it, the only permitted output is:

`data/reports/dashboard_runtime_blockers_remediation_runbook_v1.json`

Status meanings:

- `ok`: no current blockers and the safety contract is intact.
- `warning`: current read-only blockers are fully mapped to operator guidance.
- `blocked`: a critical blocker lacks a mapping or the safety contract is unsafe.

The report is runtime evidence and should not be versioned.
