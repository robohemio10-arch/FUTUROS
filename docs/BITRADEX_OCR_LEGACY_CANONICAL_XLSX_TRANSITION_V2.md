# Bitradex OCR Legacy Canonical XLSX Transition V2

## Objective

This transition replaces the failed V1 workbook assumption without changing
the approved 504-row legacy append scope. It remains a guarded,
research-only transition with no operational authority.

The V1 attempt failed before the first master replacement because
`trades_master.xlsx` is not a canonical mirror of the Parquet. Its
`trades_master_candidate` sheet is an OCR evidence workbook with a 71-column
raw lineage schema, plus a `BUILD_SUMMARY` sheet.

V1 is now recorded as `failed_pre_replace_superseded`. Every new V1 apply is
blocked before lock or backup creation with
`transition_v1_superseded_after_xlsx_layout_mismatch`.

## Canonical Source Decision

The pre-transition artifacts have distinct roles:

- `data/trades/trades_master.parquet`: sole canonical source for the existing
  3,058-row prefix.
- `data/trades/trades_master.xlsx`: `legacy_ocr_evidence_workbook`, preserved
  byte-for-byte in the mandatory backup.
- Preview V4 CSV: ordered source for the 504 approved legacy candidates.
- authoritative package metadata: source of the uniform `imported_at` value.

The raw XLSX rows are never reverse-mapped into canonical rows. The V2 target
XLSX is created from scratch from the canonical Parquet prefix and the
materialized Preview V4 tail.

## Target Contract

The post-transition target is:

- one sheet named `trades_master_canonical`;
- exactly 25 historical canonical columns;
- exactly 3,562 data rows;
- first 3,058 rows semantically equal to the pre-transition Parquet;
- final 504 rows semantically equal to Preview V4 after uniform
  `imported_at` materialization;
- semantic equality between the target XLSX and target Parquet;
- classification `research_only_legacy_non_v2`.

The target does not become Fingerprint V2 compatible and receives no identity,
deduplication, training, signal, risk, paper, live, canary, or execution
authority.

## Planner

The default command is no-write:

```powershell
python .\scripts\build_bitradex_ocr_legacy_canonical_xlsx_transition_v2.py plan --project-root . --json
```

The planner validates:

- V2 contract identity, state, authorization policy, and source hashes;
- exact pre-transition XLSX and Parquet hashes;
- exact legacy sheet set requirements;
- the full 71-column legacy OCR header;
- presence of `BUILD_SUMMARY`;
- canonical Parquet schema and 3,058-row count;
- Preview V4 count, required columns, collision guards, and source order;
- package-pinned `imported_at`;
- prefix, tail, and target semantic hashes;
- canonical target XLSX and Parquet construction;
- pre-transition policy and post-transition research-only target policy.

Candidate files are built only under an external system temporary directory
and removed before the planner returns. The plan hash excludes volatile XLSX
container bytes and includes deterministic semantic evidence.

`--write-report` may write only the JSON and Markdown plan reports under
`data/reports`.

## Apply Transaction

Apply is not authorized by this branch and must not be run during validation.
The implementation requires all of the following in a future separately
approved operation:

- the V2 authorization phrase;
- the exact V2 plan SHA-256;
- a clean recomputation of the plan;
- an exclusive V2 lock;
- byte-exact backup of both current masters;
- verified canonical candidates;
- post-apply attestation.

The V2 phrase and plan are distinct from V1. A V1 phrase or V1 plan hash has
no authority over V2.

On failure before the first replacement, the executor preserves both masters
and the backup, persists a sanitized failure report, performs no rollback, and
removes its temporary candidates and lock.

On failure after either replacement, the executor restores both masters from
the byte-exact backup, verifies their pre-transition hashes, persists rollback
evidence, and removes its temporary candidates and lock.

Failure evidence includes:

- `failed_stage`;
- sanitized `error_code`;
- `transaction_committed`;
- `master_replace_started`;
- `rollback_attempted`;
- `rollback_succeeded`;
- backup paths, hashes, and sizes;
- hashes after failure;
- `masters_preserved`;
- `report_write_performed`;
- `master_write_performed`.

## Governance

The existing legacy research-only policy remains valid for the current
3,058-row Parquet. The boundary auditor additionally recognizes the pinned V2
target semantic state, so a successful future transition does not create a
known governance gap merely because the old byte hash changed.

Both states remain:

- `research_only_legacy_non_v2`;
- non-authoritative for identity and financial decomposition;
- non-importable outside the explicit guarded transaction;
- unavailable to operational training, signals, risk, orders, paper, live, or
  canary execution.

The superseded V1 implementation and active V2 implementation are separately
hash-pinned. V1 remains executable only in synthetic tests that explicitly
construct a historical `planned_not_executed` fixture.

## Safety Boundary

The planner and normal CLI validation preserve:

- `apply_executed=false`;
- `master_write_performed=false`;
- `writes_trader_master=false`;
- `writes_xlsx=false`;
- `writes_parquet=false`;
- `writes_sqlite=false`;
- `writes_runtime=false`;
- `changes_risk=false`;
- `sends_orders=false`;
- `exchange_private_access=false`;
- `updates_qlib=false`;
- `updates_ai_shadow=false`.

The transition never assumes funding is zero and never upgrades synthetic
order identifiers into Fingerprint V2 identity.

## Validation

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest .\tests\test_bitradex_ocr_legacy_canonical_xlsx_transition_v2.py -q
python -m pytest .\tests\test_bitradex_ocr_legacy_authorized_append_v1.py -q
python .\scripts\build_bitradex_ocr_legacy_canonical_xlsx_transition_v2.py plan --project-root . --json
python .\scripts\audit_trader_master_legacy_research_only_boundary_v1.py --project-root . --no-write --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
git diff --check
git status --short
```

Do not run the V2 `apply` subcommand without a new explicit operational
authorization.
