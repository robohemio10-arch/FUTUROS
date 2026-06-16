# Runtime Freshness Producer Entrypoint Static Safety Audit V1

This audit verifies, without execution, that the manual producer entrypoints referenced
by runtime freshness contracts are present and statically compatible with the expected
operator contract.

It is intentionally read-only:

- it reads materialized contracts from `data/reports/runtime_freshness_producer_contracts_audit_v1.json`;
- if that report is absent, it falls back to the canonical internal three-producer contract;
- it parses producer scripts with Python AST and controlled text checks;
- it never imports or executes the audited producer entrypoints;
- it does not change global, runtime-evidence, or combined blocker lists.

## Covered Entrypoints

| Producer | Entrypoint | Expected artifact |
| --- | --- | --- |
| `market_data_health_audit` | `scripts/run_market_data_health_audit.py` | `data/reports/market_data_health_audit_report.json` |
| `kill_switch_state_refresh` | `scripts/set_kill_switch.py` | `data/runtime/kill_switch.json` |
| `runtime_safety_config_validation` | `scripts/validate_runtime_safety_config.py` | `data/runtime/runtime_safety_audit_config.json` |

The kill-switch refresh contract requires the manual command to keep
`--enabled true`. A general-purpose script may support other values, but this static
audit blocks the freshness refresh contract if the documented refresh path disables
the kill switch or omits the explicit safe literal.

## CLI

Dry-run JSON output:

```powershell
python scripts/audit_runtime_freshness_producer_entrypoint_static_safety_v1.py --project-root . --json
```

Materialize the report:

```powershell
python scripts/audit_runtime_freshness_producer_entrypoint_static_safety_v1.py --project-root . --write-report --json
```

With `--write-report`, the only permitted output is:

```text
data/reports/runtime_freshness_producer_entrypoint_static_safety_audit_v1.json
```

This artifact is runtime evidence and must not be treated as an authorization to run
live, canary, private exchange access, or order submission.

## Dashboard Exposure

The payload is embedded as `runtime_freshness_producer_entrypoint_static_safety` in:

- `dashboard_snapshot_build_summary.json`
- `dashboard_global_status_snapshot.json`
- `dashboard_infrastructure_snapshot.json`
- `dashboard_active_controls_snapshot.json`

The Streamlit component displays summary rows, per-entrypoint compatibility, missing
flags, critical findings, and forbidden actions. It has no execution controls.

## Safety Invariants

The payload keeps:

- `manual_execution_only=true`;
- `execution_allowed=false` on each row;
- `safe_to_execute_from_dashboard=false` on each row;
- live, canary, private exchange, and order-submission flags blocked;
- producer imports, Docker, network, risk, model, dataset, YAML/config, signals,
  orders, live/canary, and runtime logic unchanged.

Static safety is evidence quality control. It is not an operational release gate and
does not override authoritative blocker sources.
