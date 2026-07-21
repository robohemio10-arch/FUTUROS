# Decision Ledger paper observability wiring V1

## Purpose

This branch places a single Decision Ledger observability coordinator around
the RiskManager boundary used by the three paper signal producers. The wiring
is present but disabled by default. No producer, strategy, Phase14 service, or
CLI enables it.

The default contract is:

```text
enabled=false
writer_enabled=false
trade_link_enabled=false
writer_invoked=false
writes_runtime=false
paper_behavior_changed=false
```

The profile does not authorize a paper restart, live/canary behavior, orders,
private exchange access, risk changes, model promotion, or replacement of the
legacy strategy decision log.

## Shared coordinator

The three producers use the same two coordinator boundaries:

1. `prepare_before_risk_manager` copies candidates and, only when enabled,
   creates deterministic signal, candidate, correlation, feature-observation,
   and model lineage before RiskManager.
2. The existing `apply_risk_manager_gate` remains the only risk authority.
3. `finalize_after_risk_manager` maps approved and rejected results through the
   certified runtime profile, persists all projections, and only then returns
   approved signals with a certified `decision_ledger` envelope.

The producers contain no writer, projection, idempotency, lock, or envelope
logic. They publish the coordinator's returned active signals. When disabled,
the coordinator returns the RiskManager-approved signals unchanged.

The integrated producers are:

- `smartcrypto/execution/signal_producer.py`;
- `smartcrypto/qlib_engine/signal_exporter.py`;
- `smartcrypto/execution/signal_contract_guard.py`.

## Certified contracts

The implementation consumes, without modifying, these certified packages:

- `decision_ledger_v4_2`;
- `decision_ledger_runtime_profile_v1`;
- `decision_ledger_runtime_integration_v1`;
- `decision_ledger_paper_runtime_writer_v1`.

The writer is constructed only through
`create_paper_runtime_writer`. A current preflight bound to the exact profile
hash is mandatory. The coordinator cannot construct the certified writer
directly.

## Fail-closed lineage

Before RiskManager, enabled wiring creates deterministic IDs and hashes the
pre-risk candidate observation under the versioned feature contract
`paper-signal-observation-lineage-v1`. Existing authoritative values are
preserved. `model_hash` is never invented: enabled configuration requires an
explicit SHA-256. Missing or invalid certified fields cause an approved signal
projection failure and block active publication.

After RiskManager, approved decisions receive their approved stake and
leverage from the RiskManager-stamped candidate and final `ALLOW`; rejected
decisions receive zero stake/leverage and final `BLOCK`. Paper observations are
not used to alter risk.

## Persistent idempotency sink

`IdempotentDecisionLedgerRuntimeSink` wraps only a writer returned by the
paper-runtime factory. Its index is a versioned JSON artifact under the exact
writer root and stores the complete certified projection needed by Phase14.

The sink provides:

- exclusive multiprocess lock by atomic create;
- atomic index replacement;
- file and parent-directory fsync where supported;
- same key and same hash as a duplicate without ledger append;
- same key and different hash as `CriticalIdempotencyConflict`;
- reconciliation with an existing ledger after an index-write interruption;
- fail-closed malformed/corrupt index handling;
- no in-memory-only index fallback.

All projections are persisted before approved signals are returned for active
publication. Any approved projection, persistence, or envelope failure returns
an empty active set with `publication_blocked=true`.

## Strategy correlation

`SmartCryptoSignalStrategy.py` preserves these fields from the certified
active-signal envelope:

- `decision_event_id`;
- `signal_id`;
- `correlation_id`.

The identifiers are copied into dataframe observation columns and retained in
the legacy decision log. When a decision event exists, only its explicit ID is
added to `enter_tag` for authoritative Phase14 correlation. Without an
envelope, the historical `smartcrypto_long` and `smartcrypto_short` tags remain
unchanged.

No side, leverage, stake, ROI, stoploss, entry condition, or exit policy was
changed. The legacy writer remains in place.

## Phase14 trade-link adapter

The Phase14 adapter is disabled by default and reads only the exported paper DB
snapshot. When explicitly enabled, it:

- opens SQLite through URI `mode=ro`;
- sets `PRAGMA query_only=ON`;
- reads closed trades only;
- requires `decision_event_id` in `enter_tag`;
- rejects timestamp-only association;
- maps through the certified trade-link contract;
- persists through the same factory-created writer and idempotent sink;
- never updates DB rows, trades, PnL, or timestamps;
- never replays quarantined records automatically.

Rows without explicit correlation remain unlinked. A matching timestamp alone
is not evidence.

## Configuration

The versioned profile is
`config/decision_ledger_paper_observability.yml`. It is intentionally disabled
and contains no model hash placeholder. Future enablement requires a separate
review and an authoritative model SHA-256, an explicitly enabled writer
profile, a prepared canonical runtime root, verified non-root identity, and all
preflight checks.

This branch does not create the runtime root and does not start containers.

## CLIs

Both CLIs are static/read-only and do not invoke a writer:

```powershell
python scripts/validate_decision_ledger_paper_observability_wiring_v1.py --project-root . --json
python scripts/audit_decision_ledger_paper_observability_wiring_v1.py --project-root . --json
```

The validator proves the versioned profile remains disabled. The auditor
parses the three producers, verifies prepare/RiskManager/finalize ordering,
checks strategy correlation, and confirms the Phase14 adapter is present. It
does not import or execute producers, strategies, Freqtrade, containers, or
exchange code.

## Validation

```powershell
python -m compileall scripts smartcrypto tests
python -m ruff check smartcrypto/execution/decision_ledger_paper_observability_wiring_v1 scripts/validate_decision_ledger_paper_observability_wiring_v1.py scripts/audit_decision_ledger_paper_observability_wiring_v1.py tests/test_decision_ledger_paper_observability_wiring_v1.py
python -m mypy smartcrypto/execution/decision_ledger_paper_observability_wiring_v1 scripts/validate_decision_ledger_paper_observability_wiring_v1.py scripts/audit_decision_ledger_paper_observability_wiring_v1.py --ignore-missing-imports --follow-imports=skip
python -m pytest tests/test_decision_ledger_paper_observability_wiring_v1.py -q
python -m pytest tests/test_decision_ledger_payload_v4_2.py tests/test_decision_ledger_runtime_profile_v1.py tests/test_decision_ledger_runtime_integration_v1.py tests/test_decision_ledger_paper_runtime_writer_profile_v1.py -q
python scripts/validate_decision_ledger_paper_observability_wiring_v1.py --project-root . --json
python scripts/audit_decision_ledger_paper_observability_wiring_v1.py --project-root . --json
python scripts/audit_state_execution_ledger_boundary.py --project-root . --json
python scripts/audit_operational_exception_swallowing.py --project-root . --json
python scripts/generate_project_manifest.py --check
python scripts/scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r requirements-dev.lock --progress-spinner off
python -m pip_audit -r requirements-qlib.lock --progress-spinner off
git diff --check
```

All tests that exercise persistence use `tmp_path`. Repository `data/runtime`
must remain untouched.

## Dependency certification remediation

The development and test extras pin `GitPython==3.1.51`, the first version
listed by the dependency audit as fixing the advisories affecting 3.1.50.
`requirements-dev.lock` is regenerated with the repository's documented pip
resolver workflow in an isolated temporary environment. All pre-existing
transitive pins are supplied as resolver constraints, except GitPython, so an
unrelated dependency update cannot be accepted silently.

The resolved lock is rendered deterministically with the package spelling and
order already contracted by the repository. It changes only GitPython from
3.1.50 to 3.1.51, with no transitive version or package-name changes. The
`requirements-runtime.lock` and `requirements-qlib.lock` files remain outside
this development-certification change.
