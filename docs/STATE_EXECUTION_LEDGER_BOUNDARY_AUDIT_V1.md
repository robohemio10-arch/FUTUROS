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

This audit documents the existing authority boundaries among persistent state, financial ledgers, paper execution, risk decisions, operational evidence, and the read-only dashboard. It is an inventory and policy gate only. It does not move modules, change public schemas, create runtime writers, or execute audited code.

## Authority Map

- `smartcrypto.state` owns canonical runtime state, reconciliation state, and the canonical runtime financial event log.
- Explicit ledger modules own order-intent, capital-reservation, and financial-event persistence. A second writer is not accepted merely because it uses a similar filename.
- `smartcrypto.execution` owns paper execution intent and paper signal artifacts. It may consume risk decisions and delegate persistence to state/ledger authorities.
- `smartcrypto.risk` owns decisions and safety gates. It does not implicitly own execution state or financial ledgers.
- `smartcrypto.ops` owns reports, snapshots, health evidence, backup evidence, and audit outputs.
- Dashboard pages and services consume read-only snapshots. `smartcrypto/dashboard/command_bus.py` is the named fail-closed command-audit adapter and remains unable to submit orders or change risk.
- Operational scripts are typed entry points. They may write reports/evidence or delegate to a named domain authority; they do not acquire authority by writing a familiar path directly.

## Classification

`authorized_writer` identifies an existing named persistence authority. `report_writer` identifies an ops/script report or evidence output. `read_only_consumer` identifies a module with reads and no detected writes. `ambiguous_state_or_ledger_writer`, `ambiguous_dashboard_writer`, and `ambiguous_risk_writer` are high-severity findings because they create competing authority.

Cross-domain imports are evaluated directionally. Execution may consume risk/state contracts, ops may audit domain state, and dashboard may consume ops snapshots. `state -> execution` and ordinary `dashboard -> execution` dependencies are blocked because they invert the authority boundary.

## Static Operation

The auditor uses Python AST and the repository's versioned-file discovery contract. It does not import audited modules, call Docker, access the network or an exchange, dispatch notifications, read secrets, or mutate runtime artifacts. Output is deterministic and contains no generation timestamp.

```powershell
python scripts/audit_state_execution_ledger_boundary.py --project-root . --json --fail-on high
```

Exit code is non-zero when a finding meets the configured threshold or source parsing fails. Medium findings remain visible as `warning`; they are never promoted to `ok`.

## Safety

This policy does not authorize live trading, canary release, order submission, private exchange access, or risk changes. Generated JSON is stdout-only unless an operator explicitly redirects it to an ignored runtime report path.
