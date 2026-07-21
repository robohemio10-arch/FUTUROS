# Decision Ledger paper runtime writer profile V1

## Purpose

This package defines a versioned, fail-closed profile for a possible future
paper-only `DecisionLedgerWriter`. It does not connect the writer to a producer,
strategy, risk gate, feedback sync, container, scheduler, or paper runtime.

The default profile is disabled:

- `activation_state=disabled`;
- `enabled=false`;
- `runtime_write_authorized=false`;
- `operational_authority=false`;
- `runtime_integration_allowed=false`.

Profile validation is evidence only. A successful validation does not authorize
paper restart, runtime wiring, order submission, private exchange access, or a
risk change.

## Package boundary

`smartcrypto/execution/decision_ledger_paper_runtime_writer_v1/` adds a separate
policy layer and imports the certified `DecisionLedgerWriter` only in its
factory. It does not change the certified payload, mapping, or integration
packages.

The package provides:

- immutable Pydantic v2 profile, durability, health, preflight, factory, and
  quarantine contracts;
- an exact path policy rooted at
  `data/runtime/decision_ledger_paper_v1`;
- a non-root/elevation preflight;
- a profile hash that binds a factory call to the exact preflight evidence;
- a factory that refuses disabled, blocked, or stale preflight evidence;
- a sanitized `runtime_interruption_quarantine_v1_1` in-memory builder.

No directory or file is created by path validation or preflight. The focused
tests use only `tmp_path`; repository `data/runtime` is never written.

## Durability contract

The profile requires the certified writer capabilities rather than
reimplementing them:

- exclusive create lock;
- append-only JSONL;
- file fsync;
- atomic health replacement and health fsync;
- parent-directory fsync where supported;
- monotonic, fail-visible health counters;
- hashed error messages instead of raw error persistence.

The factory sets `design_only=false` only after an explicitly enabled profile,
a canonical path, a writable pre-existing root, a verified non-root identity,
and all durability/health checks pass. Merely exposing this factory does not
wire or invoke it. The validator CLI never calls the factory.

## Path policy

Paths are POSIX-style project-relative paths. Absolute paths, backslashes,
traversal, symlink components, paths outside the exact allowed root, missing
roots, non-directories, and non-writable roots are blocked. Ledger and health
files must be distinct `.jsonl` and `.json` files under the canonical root.

The branch intentionally does not create the canonical root. Directory
ownership and container volume preparation remain a separate operational
change requiring review.

## Non-root preflight

On POSIX, the preflight reads the effective UID and blocks UID 0. On Windows,
it reads the process elevation state and blocks an elevated process. If the
identity cannot be verified, the result is blocked. No privilege change is
attempted.

## Runtime interruption quarantine V1.1

The quarantine contract contains only identifiers, UTC time, failure stage,
error type, and SHA-256 evidence. It never stores a raw error message. It is
immutable and always requires operator review with automatic replay, writer
resume, runtime integration, operational authority, orders, private exchange
access, and risk changes disabled.

The builder prints the contract in memory. It has no write flag and no runtime
destination.

## Validation

```powershell
python -m compileall scripts smartcrypto tests
python -m ruff check smartcrypto/execution/decision_ledger_paper_runtime_writer_v1 scripts/validate_decision_ledger_paper_runtime_writer_profile_v1.py scripts/build_runtime_interruption_quarantine_v1_1.py tests/test_decision_ledger_paper_runtime_writer_profile_v1.py
python -m mypy smartcrypto/execution/decision_ledger_paper_runtime_writer_v1 scripts/validate_decision_ledger_paper_runtime_writer_profile_v1.py scripts/build_runtime_interruption_quarantine_v1_1.py --ignore-missing-imports --follow-imports=skip
python -m pytest tests/test_decision_ledger_paper_runtime_writer_profile_v1.py -q
python -m pytest tests/test_decision_ledger_payload_v4_2.py tests/test_decision_ledger_runtime_profile_v1.py tests/test_decision_ledger_runtime_integration_v1.py -q
python scripts/validate_decision_ledger_paper_runtime_writer_profile_v1.py --project-root . --json
python scripts/generate_project_manifest.py --check
python scripts/scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r requirements-dev.lock --progress-spinner off
git diff --check
```

The expected default validator decision is `KEEP_WRITER_DISABLED`. It is not an
authorization to restart paper or to connect a writer.

## Explicitly unchanged

This profile does not modify or call signal production, signal risk gating,
Freqtrade strategy code, Phase 14 feedback sync, Docker Compose, producer
configuration, RiskManager, Qlib, IA Shadow, exchanges, orders, active signals,
registries, models, SQLite, or runtime state.
