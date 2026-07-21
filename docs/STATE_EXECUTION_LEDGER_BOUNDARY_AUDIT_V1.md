# State, Execution, and Ledger Boundary Audit V1

policy_status: active
paper_only: true
shadow_only: true
live_trading_enabled: false
order_submission_enabled: false
real_order_submission_enabled: false
exchange_private_access: false
sends_orders: false
changes_risk: false

## Purpose

This static audit documents authority boundaries among persistent state,
financial ledgers, paper execution, risk decisions, operational evidence and
the read-only dashboard. It does not import audited modules, move ownership,
change public schemas, create runtime writers or execute application code.

## Authority Map

- `smartcrypto.state` owns canonical runtime and reconciliation state.
- Explicit ledger modules own order-intent, capital-reservation and financial
  event persistence.
- `smartcrypto.execution` owns paper intents and paper signal artifacts, while
  delegating state and ledger persistence to named authorities.
- `smartcrypto.risk` owns decisions and safety gates, not execution state.
- `smartcrypto.ops` owns reports, snapshots, health and audit evidence.
- Dashboard modules consume read-only snapshots. The documented command-bus
  adapter remains unable to submit orders or change risk.
- Scripts may write reports/evidence or delegate to a named domain authority.

An undeclared writer does not acquire authority because its filename resembles
an existing module.

## Exact Scoped Authorities

Decision Ledger validation and schema generation have five narrowly scoped
sandbox/design-only authorities. A match requires literal equality of path,
function or class context, and operation. The registry does not use wildcard,
glob, prefix, substring, regex or directory authorization.

| Exact path | Exact function | Operation | Authority | Classification |
| --- | --- | --- | --- | --- |
| `scripts/validate_decision_ledger_payload_v4_2.py` | `_atomic_write_json` | `write_text` | `decision_ledger_payload_validation_artifact_writer` | `sandbox_validation_artifact_writer` |
| `scripts/validate_decision_ledger_runtime_integration_v1.py` | `write_json` | `write_text` | `decision_ledger_runtime_integration_validation_artifact_writer` | `sandbox_validation_artifact_writer` |
| `scripts/validate_decision_ledger_runtime_profile_v1.py` | `atomic_write_json` | `write_text` | `decision_ledger_runtime_profile_validation_artifact_writer` | `sandbox_validation_artifact_writer` |
| `smartcrypto/execution/decision_ledger_runtime_profile_v1/schema.py` | `write_runtime_profile_schema` | `write_text` | `decision_ledger_runtime_profile_schema_writer` | `design_schema_artifact_writer` |
| `smartcrypto/execution/decision_ledger_v4_2/schema.py` | `write_payload_json_schema` | `write_text` | `decision_ledger_payload_schema_writer` | `design_schema_artifact_writer` |

The three validators write only sandbox validation evidence. The two schema
writers write only static design schemas. Every scoped contract declares:

```text
boundary=sandbox_design_only
runtime_authority=false
operational_state_authority=false
financial_ledger_authority=false
paper_restart_authority=false
```

A different operation, another function in the same file, a sibling filename,
or any undeclared path bypasses no control. It proceeds through the original
conservative `target_kind()` and writer classification.

## Conservative Classification

`authorized_writer` identifies an existing named persistence authority.
`report_writer` identifies an ops/script report or evidence output.
`read_only_consumer` has reads and no detected writes. Ambiguous state, ledger,
dashboard and risk writers remain high severity because they may create a
competing operational authority.

Cross-domain imports are directional. Execution may consume risk/state
contracts, ops may audit domain state, and dashboard may consume ops snapshots.
`state -> execution` and ordinary `dashboard -> execution` remain blocked.

## Static Operation

The auditor uses Python AST and versioned-file discovery. It does not import or
execute scanned modules, call Docker, access a network or private exchange,
dispatch notifications, read secrets or mutate runtime artifacts. Output is
deterministic and has no generation timestamp.

```powershell
python scripts/audit_state_execution_ledger_boundary.py `
  --project-root . `
  --json `
  --fail-on high
```

Exit code is non-zero when a finding reaches the configured threshold or source
parsing fails. Medium findings remain visible as `warning`.

## Safety

These scoped authorities grant no operational integration, paper restart,
runtime state, financial ledger, order, risk, Freqtrade or private-exchange
authority. They do not authorize live or canary release. The auditor emits JSON
to stdout only unless an operator redirects it to an ignored evidence path.
