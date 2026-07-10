# Paper Autotrain Feedback Gap Remediation Plan V1

## Purpose

This component converts the existing `paper_autotrain_feedback_gap_diagnostics_v1` evidence into a deterministic remediation plan. It confirms which closed paper trades are absent from feedback, separates eligible, validation-blocked, conflicting, and already-present populations, and assigns stable idempotency keys for a possible future implementation branch.

It does not backfill anything. The decision `PLAN_ONLY_NO_BACKFILL` means that the evidence is sufficient to describe future work, not that the work has been authorized or executed.

## Inputs

The default input is:

```text
data/reports/paper_autotrain_feedback_gap_diagnostics_v1.json
```

The accepted source schema is exactly `paper_autotrain_feedback_gap_diagnostics_v1`. A missing, malformed, stale, non-OK, or count-inconsistent source fails closed as `BLOCKED_SOURCE_NOT_FRESH`.

An eligible missing record must satisfy all conditions:

- classification is `missing_in_feedback`;
- both existing validation stages would pass;
- paper DB and closed-trades CSV match;
- the paper DB source is fresh against the CSV;
- the source diagnostic reports no conflicting groups.

Eligibility only produces `WOULD_CREATE_FEEDBACK_EVENT_IN_FUTURE_BRANCH`. No event is created by this package.

## Usage

No-write default:

```powershell
python scripts\build_paper_autotrain_feedback_gap_remediation_plan_v1.py --project-root . --json
```

Explicit report-only write:

```powershell
python scripts\build_paper_autotrain_feedback_gap_remediation_plan_v1.py --project-root . --write-report --json
```

Only these outputs are accepted:

```text
data/reports/paper_autotrain_feedback_gap_remediation_plan_v1.json
data/reports/paper_autotrain_feedback_gap_remediation_plan_v1.md
```

Custom output paths must remain under `data/reports` and retain the expected `.json` and `.md` suffixes.

## Decisions

- `PLAN_ONLY_NO_BACKFILL`: source is fresh, matched, conflict-free, and validation-clean.
- `BLOCKED_CONFLICTS_REQUIRE_RECONCILIATION`: source groups conflict and require separate reconciliation.
- `BLOCKED_VALIDATION_REJECTION_REQUIRES_REVIEW`: one or more missing records fail an existing validation stage.
- `BLOCKED_SOURCE_NOT_FRESH`: diagnostics are absent, malformed, stale, inconsistent, or outside the accepted contract.

`plan_hash` is computed from canonical plan content and excludes timestamps and write-request state. `plan_id` is derived from that hash. Each missing record receives an independent deterministic `idempotency_key` based only on its source identity.

## Safety Boundary

The planner is paper-only, shadow-only, research-only, and read-only. It never writes feedback, microbatches, Parquet, SQLite, models, registries, runtime state, watermarks, signals, or trading configuration. It does not train or promote a model, send orders, access a private exchange, change risk, register a scheduler, or modify Freqtrade, Qlib runtime, or IA Shadow runtime.

The explicit `--write-report` flag grants authority only for the canonical JSON and Markdown evidence under `data/reports`.

## Validation

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests\test_paper_autotrain_feedback_gap_remediation_plan_v1.py -q
python scripts\build_paper_autotrain_feedback_gap_remediation_plan_v1.py --project-root . --json
python scripts\build_paper_autotrain_feedback_gap_remediation_plan_v1.py --project-root . --write-report --json
python scripts\generate_project_manifest.py --check
python scripts\scan_versioned_secrets.py --project-root . --json
git diff --check
git status --short
git status --short -- data
```
