# Paper Autotrain Feedback Gap Backfill Dry-Run V1

## Purpose

This component validates a possible future backfill of paper autotrain feedback gaps without applying it. It consumes the approved remediation plan, materializes candidate feedback events only in memory, validates their schema and deterministic identity, checks them against the current feedback JSONL, and produces audit evidence.

`DRYRUN_READY_NO_BACKFILL` is not permission to write feedback. It means only that the simulated candidates are internally consistent, absent from the current feedback store, and tied to the expected source plan hash.

## Inputs

Default sources:

```text
data/reports/paper_autotrain_feedback_gap_remediation_plan_v1.json
data/feedback/paper_autotrain_daily_quarantine_feedback_events_v1.jsonl
```

The plan must have:

- schema `paper_autotrain_feedback_gap_remediation_plan_v1`;
- `status=ok`;
- `decision=PLAN_ONLY_NO_BACKFILL`;
- zero blocked events;
- an eligible-record count equal to `planned_feedback_event_count`;
- the expected semantic `plan_hash`.

The default expected hash is the approved plan:

```text
7a566e9359c55c42d4f9606e35b4359cb0bad345be79ce978c8e848b4f0aaacb
```

An explicit `--expected-plan-hash` may be used only to audit a separately approved plan. Hash comparison is case-insensitive; the source value remains visible in the report.

## Usage

No-write default:

```powershell
python scripts\build_paper_autotrain_feedback_gap_backfill_dryrun_v1.py --project-root . --json
```

Report-only write:

```powershell
python scripts\build_paper_autotrain_feedback_gap_backfill_dryrun_v1.py --project-root . --write-report --json
```

The only permitted outputs are:

```text
data/reports/paper_autotrain_feedback_gap_backfill_dryrun_v1.json
data/reports/paper_autotrain_feedback_gap_backfill_dryrun_v1.md
```

## Determinism and Idempotency

Each candidate retains the planner's `idempotency_key`. Its `event_hash` is SHA-256 over canonical event content excluding the hash field itself. Events are sorted by `close_time_utc`, `dedup_key`, and `idempotency_key`. `dryrun_hash` covers the ordered event hashes, expected/source plan hashes, schema, and decision, while excluding timestamps and write state.

Existing feedback is checked by available event hash, idempotency key, closed-trades CSV order ID, and paper DB trade ID. Missing, malformed, or unreadable feedback evidence blocks the dry-run because absence cannot be proven safely.

## Decisions

- `DRYRUN_READY_NO_BACKFILL`: simulation is valid and remains unapplied.
- `BLOCKED_PLAN_NOT_READY`: plan or feedback evidence is missing, invalid, or not approved.
- `BLOCKED_SOURCE_PLAN_HASH_MISMATCH`: semantic plan hash differs from the expected hash.
- `BLOCKED_SCHEMA_VALIDATION_FAILED`: one or more simulated events violate the candidate schema.
- `BLOCKED_DUPLICATE_SIMULATED_EVENTS`: simulated hashes or idempotency keys repeat internally.
- `BLOCKED_EVENT_ALREADY_EXISTS`: a candidate matches the current feedback store.

## Safety Boundary

This package is paper-only, shadow-only, research-only, read-only, and dry-run-only. It has no feedback writer and does not perform backfill, create microbatches, change watermarks, train, promote, write models or registries, alter runtime, touch SQLite or operational Parquet, send orders, access a private exchange, or change Freqtrade, RiskManager, Qlib runtime, or IA Shadow runtime.

`--write-report` grants authority only for the JSON and Markdown evidence under `data/reports`.

## Validation

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests\test_paper_autotrain_feedback_gap_backfill_dryrun_v1.py -q
python scripts\build_paper_autotrain_feedback_gap_backfill_dryrun_v1.py --project-root . --json
python scripts\build_paper_autotrain_feedback_gap_backfill_dryrun_v1.py --project-root . --write-report --json
python scripts\generate_project_manifest.py --check
python scripts\scan_versioned_secrets.py --project-root . --json
git diff --check
git diff --cached --check
git status --short
git status --short -- data
```
