# Runtime Integrity and Traceability V2

## Status

This branch establishes an institutional integrity boundary for shared
paper/shadow reports and an explicit-ID traceability ledger.

The implementation is:

- paper-only;
- shadow-only;
- research-only;
- without operational authority;
- disabled as a trading control;
- unable to submit orders;
- unable to change risk;
- unable to publish active signals;
- unable to promote models.

It does not restart or recreate containers and does not activate runtime wiring.

## Atomic durability contract

All migrated JSON, JSONL and Markdown writers delegate to
`smartcrypto.runtime.integrity_traceability_v2.atomic_writer`.

The write sequence is:

1. Resolve the target against an explicit root allowlist.
2. Reject traversal and existing symlink components.
3. Serialize writers by an in-process lock and a per-target process lock.
4. Create an exclusive temporary with `mkstemp` in the target directory.
5. Write all bytes.
6. Flush and `fsync` the temporary.
7. Close the temporary.
8. Apply restrictive file permissions.
9. Promote with `os.replace`.
10. Retry only transient Windows sharing violations, with a bounded limit.
11. `fsync` the parent directory when the platform supports it.
12. Remove only the temporary owned by the current invocation.

An error before promotion preserves the previous valid target. A parent
directory durability error after promotion is reported with
`promoted=true`; it is never converted into success.

JSONL append is implemented as a validated, locked, atomic materialization.
Existing malformed JSONL is blocked rather than extended.

## Consistent reader contract

Confirmed concurrent readers use `read_json_consistent`.

The reader:

- rejects symlinks and paths outside authorized roots;
- retries only for a bounded interval;
- tolerates the transient Windows sharing window around `os.replace`;
- validates UTF-8 and JSON;
- rejects empty, missing, non-regular and persistently invalid files;
- returns no stale synthetic payload.

Retry is defense in depth. It does not replace the atomic writer.

## Writer inventory

The following writer families were migrated without changing their output
paths or financial schemas:

| Domain | Writer modules | Shared artifact class |
| --- | --- | --- |
| Trade notifications | `smartcrypto/ops/trade_event_notifications.py` | daemon report JSON |
| Phase 14 | `scripts/export_freqtrade_paper_db_snapshot.py`, `smartcrypto/data/paper_trade_lifecycle.py` | snapshot and feedback reports |
| Qlib paper | `smartcrypto/qlib_engine/common.py`, `smartcrypto/qlib_engine/paper_refresh_supervisor.py` | refresh, prediction and supervisor reports |
| Phase 13 | `smartcrypto/execution/signal_producer.py`, `smartcrypto/execution/signal_store.py` | signal producer reports and paper signal artifacts |
| IA Shadow evidence | `smartcrypto/ml/model_decision_logger.py`, `smartcrypto/ml/outcome_tracker.py` | decision/outcome JSONL and reports |
| Financial research | `smartcrypto/ml/ai_shadow_financial_evaluation.py`, `smartcrypto/ml/monte_carlo_risk_simulation.py` | threshold and Monte Carlo reports |
| Runtime evidence | `smartcrypto/ops/runtime_evidence_pack.py` | evidence and readiness JSON |
| Dashboard evidence | `smartcrypto/ops/dashboard_real_paper_sources/builder.py` | read-only dashboard snapshot |
| Paper observation | `smartcrypto/ops/paper_candidate_filter_runtime_observation_pack/observation_pack.py` | JSON and Markdown evidence |
| Phase summaries | `scripts/collect_phase16_summary.py`, `scripts/collect_phase17_summary.py` | summary JSON |

The certified Phase 14 SQLite snapshot writer remains a separate binary
artifact boundary. Its existing same-directory temporary, backup, promotion
and fsync operations are represented by exact static-audit authorities. The
new JSON writer does not replace SQLite's backup API.

## Reader inventory

The confirmed shared-report readers include:

- the trade-event notification dashboard panel;
- Phase 14 feedback sync healthcheck;
- Qlib refresh supervisor healthcheck;
- the real-paper dashboard snapshot builder;
- runtime evidence aggregation;
- Phase 16 and Phase 17 summary collectors;
- downstream dashboard and readiness snapshot consumers.

The directly migrated readers use the consistent reader. Downstream readers
that already have their own bounded, fail-closed loader retain that contract.

## Schemas preserved

This branch changes the persistence mechanism, not report meaning.

Preserved output classes include:

- notification daemon state and event counters;
- Phase 13 and Phase 14 status and safety flags;
- Qlib freshness and supervisor status;
- IA Shadow decision and outcome records;
- Monte Carlo and financial threshold metrics;
- dashboard and readiness evidence;
- observation-pack JSON and Markdown.

No PnL, fee, stake, leverage, risk, signal side, ROI, stoploss or model field is
rewritten by the atomic utility.

## Correlation ledger

The traceability ledger is materialized paper/shadow evidence. It is not the
financial Decision Ledger and has no execution authority.

Each complete chain requires all eight explicit source event types:

1. `market_event`
2. `prediction`
3. `shadow_decision`
4. `risk_decision`
5. `signal`
6. `freqtrade_trade`
7. `feedback`
8. `training_sample`

The final record requires:

- `market_event_id`;
- `prediction_id`;
- `model_version`;
- `shadow_decision_id`;
- `risk_decision_id`;
- `signal_id`;
- `freqtrade_trade_id`;
- one or more `order_ids`;
- `feedback_event_id`;
- `training_sample_id`.

`decision_event_id` is preserved when supplied by the certified Decision
Ledger lineage, but it is not invented.

### Existing correlation sources

The implementation reuses the semantics already present in:

- Decision Ledger V4.2 and its paper observability lineage;
- Phase 13 signal production;
- SmartCryptoSignalStrategy correlation preservation;
- Phase 14 explicit trade-link evidence;
- IA Shadow model decision logger;
- IA Shadow outcome tracker.

The builder accepts source events explicitly. It does not import or execute
these operational modules to discover IDs.

### Quarantine rules

A chain is quarantined when:

- `correlation_id` is absent;
- matching is timestamp-only;
- a source event is invalid;
- a source type is missing or duplicated;
- a required identifier is missing;
- identifiers inside one chain disagree;
- an event identifier belongs to more than one chain.

`model_version` may legitimately repeat across trades and is therefore
required but not treated as a globally unique event ID.

No missing identifier is synthesized. A timestamp, nearest-time window or
paper trade chronology is never used as a replacement identifier.

## Static writer audit

`audit_runtime_shared_report_writers_v2.py` performs AST-only inspection. It
does not import or execute audited modules.

It detects:

- `Path.write_text` and `Path.write_bytes`;
- writable `open`;
- writable `Path.open`;
- `json.dump`;
- direct or injected replace operations;
- direct or injected temporary creation;
- direct or injected fsync operations;
- low-level lock initialization writes.

Known producer modules are inspected for every direct write. Other modules
are blocked when a direct write targets a shared path literal or a statically
bound path variable.

Authorities require an exact tuple:

`path + function/class + operation + authority_id + justification`

Wildcards, directory grants and prefix-based grants are forbidden. Similar
files or additional writer functions do not inherit authority.

## CLI usage

Static no-write audit:

```powershell
python .\scripts\audit_runtime_shared_report_writers_v2.py `
  --project-root . `
  --json
```

Explicit correlation input, no write:

```powershell
python .\scripts\build_runtime_integrity_traceability_ledger_v2.py `
  --project-root . `
  --input .\events.json `
  --json
```

Research report materialization requires `--write-report` and is restricted
to the project `data/` boundary:

```powershell
python .\scripts\build_runtime_integrity_traceability_ledger_v2.py `
  --project-root . `
  --input .\events.json `
  --report .\data\reports\runtime_integrity_traceability_ledger_v2.json `
  --write-report `
  --json
```

Runtime reports generated by these commands remain ignored artifacts and must
not be committed.

## Validation

```powershell
python -m compileall -q scripts smartcrypto tests
python -m pytest tests\test_runtime_integrity_and_traceability_v2.py -q
python .\scripts\audit_runtime_shared_report_writers_v2.py --project-root . --json
python .\scripts\build_runtime_integrity_traceability_ledger_v2.py --project-root . --json
python .\scripts\generate_project_manifest.py --project-root . --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
git diff --check
git status --short -- data
```

## Operational boundary

This branch does not:

- enable a writer profile in running containers;
- restart paper services;
- change Freqtrade strategy or configuration;
- change RiskManager;
- change stake, leverage, ROI or stoploss;
- update Qlib or IA Shadow active models;
- submit or simulate exchange orders;
- access private exchange endpoints;
- write a financial ledger;
- publish `active_freqtrade_signals.json`;
- promote any model or registry entry.

Activation, deployment and container lifecycle remain outside B01.
