# Runtime Freshness Post-Refresh Evidence Gate V1

## Objective

This read-only gate validates whether manually refreshed artifacts for the three runtime freshness producers can be accepted as materialized evidence. It detects stale or invalid artifacts, blocker-list bypasses, and unsafe state. It never executes producers and never authorizes live, canary, or order submission.

## Producer Contract, Manual Execution, And Gate

- Producer contracts define what an operator must run manually outside Streamlit.
- Manual execution materializes or refreshes the expected artifact.
- The post-refresh gate checks the artifact, source-health blocker state, and safety flags after an external snapshot rebuild.

The gate is an evidence acceptance layer, not an operational release mechanism.

## Artifact Criteria

### Market Data Health

- Artifact: `data/reports/market_data_health_audit_report.json`
- Timestamp: `generated_at_utc`
- Health: JSON parses and status is `OK` or equivalent.
- Freshness: timestamp age is at or below the contract limit.
- Blocker: `src_data_reports_market_data_health_audit_report_json:STALE` is absent after snapshot rebuild.

### Kill Switch

- Artifact: `data/runtime/kill_switch.json`
- Timestamp: `updated_at`
- Health: JSON parses and `enabled=true`.
- Freshness: timestamp age is at or below the contract limit.
- Blocker: `src_data_runtime_kill_switch_json:STALE` is absent after snapshot rebuild.
- Safety: live, canary, private exchange, and order flags remain false.

### Runtime Safety Audit Config

- Artifact: `data/runtime/runtime_safety_audit_config.json`
- Timestamp: `generated_at_utc`
- Health: JSON parses and safety semantics remain paper/shadow.
- Freshness: timestamp age is at or below the contract limit.
- Blocker: `src_data_runtime_runtime_safety_audit_config_json:STALE` is absent after snapshot rebuild.

## Bypass Indicators

The gate blocks when it detects any of these conditions:

- artifact timestamp is fresh but the blocker remains present;
- blocker disappeared while the artifact is stale, missing, invalid, or unsafe;
- `global_blocking_reasons` is empty while source health remains `BLOCKED`;
- dashboard is `OK` with a critical artifact stale, missing, invalid, or unsafe;
- `kill_switch.enabled=false`;
- any live, canary, order, private exchange, or sends-orders flag is true;
- manual closeout is allowed while a gate row is blocked.

## Manual Closeout

Manual closeout is acceptable only when all three gate rows pass, no bypass indicator exists, safety is preserved, and blocker absence is explained by valid refreshed evidence. Seven days remain diagnostic only. Thirty continuous valid days remain the minimum readiness requirement, with no automatic live/canary/order release.

## Prohibited Actions

- Do not execute producers from the dashboard or this gate CLI.
- Do not call Docker, subprocesses, network, or private exchange APIs.
- Do not edit blocker lists, snapshots, `.env`, YAML/config, risk, models, datasets, or signals.
- Do not disable the kill switch.
- Do not send notifications or orders.
- Do not infer operational readiness from a passing freshness gate.

## CLI

Read-only evaluation:

```powershell
python scripts/audit_runtime_freshness_post_refresh_evidence_gate_v1.py --project-root . --json
```

Explicit report materialization:

```powershell
python scripts/audit_runtime_freshness_post_refresh_evidence_gate_v1.py --project-root . --write-report --json
```

Without `--write-report`, no file is written. With it, the only permitted output is `data/reports/runtime_freshness_post_refresh_evidence_gate_v1.json`, which is runtime evidence and must not be versioned.
