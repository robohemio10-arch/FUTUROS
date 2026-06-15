# Runtime Freshness Producer Contracts and Manual Closeout V1

## Objective

This read-only institutional layer defines manual external execution contracts for the three documented producers that remediate current freshness and source-health blockers. It does not execute producers, mutate runtime state, or authorize live, canary, or order submission.

## Contracted Producers

### Market Data Health

- Domain: `market_data`
- Producer: `scripts/run_market_data_health_audit.py`
- Expected artifact: `data/reports/market_data_health_audit_report.json`
- Expected timestamp: `generated_at_utc`
- Entry: the source is stale/critical stale and all documented market-health inputs have been reviewed.
- Closeout: the report is valid, healthy, current, and its source ID is absent from `global_blocking_reasons` after an external snapshot rebuild.

### Kill Switch Runtime

- Domain: `portfolio_risk`
- Producer: `scripts/set_kill_switch.py`
- Expected artifact: `data/runtime/kill_switch.json`
- Expected timestamp: `updated_at`
- Entry: the source is stale/critical stale and the operator confirms that `enabled=true` must be preserved.
- Closeout: the kill switch remains enabled, the timestamp is current, and its source ID is absent after snapshot rebuild.

### Runtime Safety Audit Config

- Domain: `active_controls`
- Producer: `scripts/validate_runtime_safety_config.py`
- Expected artifact: `data/runtime/runtime_safety_audit_config.json`
- Expected timestamp: `generated_at_utc`
- Entry: the source is stale/critical stale and the canonical paper configuration has been reviewed without modification.
- Closeout: validation remains paper/shadow safe, live and orders remain disabled, and the source ID is absent after snapshot rebuild.

## Manual External Execution

All command hints are text only. Execution must occur manually outside Streamlit and outside both audit CLIs. The operator must use only the documented project producer, review its inputs, and stop on missing inputs, invalid output, unsafe flags, or ambiguous state.

The dashboard is a presentation surface. The contracts describe entry, evidence, verification, abort, and manual closeout conditions; they are not an execution or release mechanism.

## Post-Refresh Validation

For every contract:

1. Inspect and parse the expected artifact.
2. Verify the contract-specific timestamp is valid and current.
3. Verify semantic safety, including `enabled=true` for the kill switch.
4. Rebuild dashboard snapshots externally.
5. Verify blocker lists changed only because new materialized evidence became healthy.
6. Re-run producer, closeout, operator-pack, runbook, semantic, and readiness audits.
7. Keep live, canary, private exchange access, and order submission disabled.

## Manual Closeout

`manual_closeout_allowed` remains `false` while any critical freshness blocker is stale or blocked, any required contract is missing/incomplete, any required input is unavailable, or any safety flag is inconsistent.

Closeout is manual and source-specific. It removes no blocker directly and provides no automatic release. Seven days of evidence remain diagnostic only; thirty continuous valid days remain the minimum readiness requirement without automatic live, canary, or order release.

## Prohibited Actions

- Do not execute producers from the dashboard or audit CLIs.
- Do not call Docker, subprocesses, network, or private exchange APIs.
- Do not disable the kill switch or edit blocker lists.
- Do not modify `.env`, YAML/config, risk, models, datasets, or signals.
- Do not send notifications or orders.
- Do not infer release authority from freshness closeout.

## CLI

Read-only evaluation:

```powershell
python scripts/audit_runtime_freshness_producer_contracts_v1.py --project-root . --json
```

Explicit report materialization:

```powershell
python scripts/audit_runtime_freshness_producer_contracts_v1.py --project-root . --write-report --json
```

Without `--write-report`, no file is written. With it, the only permitted output is `data/reports/runtime_freshness_producer_contracts_audit_v1.json`, which is runtime evidence and must not be versioned.
