# Bitradex Dependency Boundary Cleanup V1

policy_status: active
paper_only: true
shadow_only: true
live_trading_enabled: false
order_submission_enabled: false
real_order_submission_enabled: false
exchange_private_access: false
sends_orders: false
changes_risk: false
runs_ocr: false
imports_trades: false

## Boundary

Bitradex is a public-data collection and controlled OCR ingestion pipeline. It is not trading runtime. The collector writes only its own raw, output, runtime, log, and collector SQLite paths. OCR output remains staging or review material until a separate official import gate approves it.

The dashboard consumes snapshots and reports read-only. Dashboard pages, components, and loaders do not import OCR/import implementations and cannot write staging, datasets, models, risk state, active signals, readiness, canary, or live state.

## Authorized Scripts

OCR and staging writers:

- `scripts/ocr_bitradex_images_to_review.py`
- `scripts/repair_price_scale_ocr_anomalies.py` for offline repair output only

Official trades master writers:

- `scripts/apply_bitradex_ocr_orderid_synthetic_v5_to_trades_master.py`
- `scripts/import_trades_incremental.py`
- `scripts/large_trades_import_quality_gate.py` only after its dry-run, backup, and quality gates

Official dataset rebuild authority remains separate from OCR/import:

- `scripts/rebuild_phase5_datasets.py`
- `scripts/build_training_dataset.py`
- `scripts/build_quality_gated_shadow_compatible_dataset_v1.py`

The OCR scripts do not directly write `training_dataset`, the quality-gated dataset, active signals, model registry, or model artifacts. Dataset rebuilds must follow the documented Phase 5 sequence after an approved import.

## Read And Write Authority

- Collector: may write collector-local public candle staging and its collector SQLite. It cannot access a private exchange or Freqtrade DB.
- OCR review: may read local images and write review/staging files and reports. It cannot import trades by itself.
- Controlled import: may read approved staging. Only the listed official scripts may write the official trades master, with preview, deduplication, backup, and audit evidence.
- Phase 5: may rebuild official derived datasets under its existing contracts. OCR does not acquire this authority.
- Ops: may read source status and write runtime reports/evidence.
- Dashboard: read-only snapshot/report consumer.
- Trading, risk, Qlib, and IA Shadow: may consume validated downstream contracts, but do not import the collector or OCR implementation.

Audit SQLite used by the collector remains collector-local. This policy does not authorize access to or mutation of the operational Freqtrade database.

## Static Auditor

`scripts/audit_bitradex_dependency_boundary.py` uses AST and versioned-file discovery. It does not import audited modules, execute OCR, open runtime data, call Docker or the network, access an exchange, dispatch notifications, or read secrets.

```powershell
python scripts/audit_bitradex_dependency_boundary.py --project-root . --json
```

The output is deterministic and has no timestamp. Any real high or critical dependency/write violation blocks the audit.

## Backlog

The collector currently declares bounded dependency ranges rather than exact hash locks. The auditor reports this as medium severity. Exact hashes belong to the dedicated lockfile-hardening work and must never be invented. The offline price-scale repair utility also lives under the `smartcrypto.ml` namespace; it remains documented as offline-only and cannot train or promote a model.

## Safety

This policy does not enable live trading, canary release, orders, private exchange access, risk changes, model changes, OCR execution, trade import, or dataset mutation. Runtime-generated reports and data remain ignored by Git.
