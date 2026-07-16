# Bitradex OCR Legacy Authorized Append V1

## Purpose

This package implements a guarded, one-shot transition for a possible future append
of 504 historical Bitradex OCR rows to the XLSX and Parquet Trader Master pair. The
current branch only plans and validates the transition. It does not authorize or run
`apply`.

The transition is separate from the research-only legacy policy. That policy remains
unchanged and continues to deny generic writers, importers, operational consumers,
fingerprint generation, training, signals, risk decisions, and orders.

## Modes

`plan` is the default and is no-write. It validates the versioned contracts, pinned
source-code hashes, Preview V4 evidence, 25-column historical schema, current master
hashes, row accounting, collision guards, and deterministic semantic hashes. The
canonical plan contains no generated timestamp and is hashed with sorted compact JSON.

The 504 empty `imported_at` cells are materialized only in memory from authoritative
package metadata:

- source:
  `data/staging/bitradex_ocr/package_20260714_151816/ORDERID_SYNTHETIC_V5_SUMMARY.json`
- JSON field: `finalized_at_utc`
- SHA-256:
  `af516464f3af30bbad94fd406530c9912c016c8b124c42d80e0de75af800d4ce`
- value: `2026-07-14T19:49:13.500939+00:00`

The value describes finalization of the OCR V5 ingestion package. It is not an
economic trade timestamp or evidence from an individual screenshot. The loader
requires an in-project regular JSON file, rejects symlinks, pins its exact hash and
value, and requires explicit UTC. Runtime clocks, filesystem timestamps, trade
fields, and the batch token are prohibited fallback sources. The Preview CSV is
never rewritten.

```powershell
python scripts/build_bitradex_ocr_legacy_authorized_append_v1.py plan `
  --project-root . `
  --json
```

`apply` exists for a later, separately authorized command. It requires both the exact
plan SHA-256 and the exact authorization phrase. Those checks occur before lock or
backup creation. The executor never consults Git and has no network, exchange, risk,
model, signal, Qlib, IA Shadow, Freqtrade, or SQLite dependency.

Technical materialization readiness does not grant execution authority. The plan
always reports `apply_allowed=false`; an apply still requires the separate explicit
subcommand and both authorization gates.

`verify` performs the post-write row-count attestation used by the transaction.

## Transaction protocol

After explicit apply preflight, the executor acquires an exclusive `O_EXCL` lock,
creates byte-exact and fsynced backups, constructs both candidates beside their
destinations, and validates the prefix, candidate tail, schema, row counts, and
semantics. It replaces Parquet then XLSX with `os.replace`. Any failure after the first
replace restores both masters through temporary files and verifies the original
hashes. A pre-existing lock is never removed.

No single atomic operation exists for two files; the verified backup and rollback
protocol is therefore part of the contract, not an optional recovery path.

## Historical limitations

Funding remains `null`, meaning unavailable rather than zero. It is never derived as a
residual. Synthetic order IDs remain lineage aliases and collision evidence only; they
never become V2 identity.

The real Preview V4 CSV carries an empty `imported_at` value on all 504 rows. The
planner records the before/after missing counts and applies the single source-backed
package value in memory. Likewise, `source_file` is recorded as package provenance
rather than treated as per-trade identity; `_dedup_key` and
`_relaxed_dedup_key` remain the enforced collision guards.

## Governance

The boundary auditor recognizes only source paths enumerated by the transition
contract and only while their SHA-256 values, transition state, default no-write mode,
and safety flags remain valid. This narrow classification is informational and does
not change the main research-only decision. Any drift removes the exception; writers
outside the hash-pinned allowlist remain HIGH or CRITICAL.

## Current authority

- `MASTER_WRITE_EXECUTED=false`
- `IMPORT_EXECUTED=false`
- `BACKUP_CREATED=false`
- `APPLY_EXECUTED=false`
- `changes_risk=false`
- `sends_orders=false`
- `exchange_private_access=false`

The future apply command must not be run until a separate review explicitly approves
the final plan hash.
