# Qlib V3 Reproducible Freeze Epoch V1

## Purpose

This branch is a P0 remediation required because Qlib V2 cannot be reconstructed
exactly from the surviving frozen artifacts. V2 remains historical and immutable.
V3 creates a new research-only epoch whose treatment assignment can be independently
reproduced without consulting mutable current datasets or current source code.

## Non-negotiable invariants

- V2 artifacts and ledgers are never rewritten.
- No V2 signal is backfilled into V3.
- No fuzzy or nearest-timestamp identity matching is permitted.
- Freqtrade, RiskManager, live/canary, order submission and private exchange access
  remain unchanged and disabled.
- The V3 source datasets are copied to immutable content-addressed Parquet snapshots
  before cohort derivation or training.
- Exact fit and calibration trade IDs are persisted in deterministic order.
- The semantic dataset, imputed matrices, medians, calibration evidence, model,
  threshold and policy are frozen independently.
- The Git source tree and dependency locks are frozen as implementation lineage.

## Freeze and activation are separate

The branch deliberately uses two phases.

### Phase A — certified freeze

1. Snapshot authoritative outcome and PIT market sources.
2. Freeze Git commit/tree, source archive and lockfile hashes.
3. Derive fit/calibration cohorts once.
4. Persist exact cohort IDs and canonical dataset/matrix artifacts.
5. Train the native Qlib/LightGBM model.
6. Freeze calibration, threshold and policy.
7. Run rebuild #1 in a fresh process from the frozen Git source archive.
8. Run rebuild #2 in another fresh process from the same frozen archive.
9. Require semantic equality across both rebuilds.
10. Copy and verify the complete external checkpoint.
11. Publish the epoch atomically.

A certified freeze has no prospective start yet. Its state is
`READY_FOR_ACTIVATION`.

### Phase B — explicit prospective activation

Activation first performs another complete two-process verification. Only after that
passes is an activation sidecar written with a future `prospective_start_utc`.
The default safety delay is 60 seconds and cannot be configured below 30 seconds.
No event before that boundary is admissible.

This avoids the V2 failure mode where a prospective boundary could exist before the
freeze had finished being certified.

## Model reproducibility contract

Two identities are intentionally separated:

- `model_artifact_sha256`: byte integrity of the serialized LightGBM text artifact.
- `model_semantic_fingerprint`: canonicalized LightGBM tree semantics.
- `treatment_semantic_fingerprint`: model semantics + exact threshold + ordered
  feature contract + eligibility policy.

Byte reproducibility is reported but treatment reproducibility is the mandatory
scientific gate. A harmless serialization-byte difference cannot silently change the
semantic contract, while a semantic change always blocks the freeze.

## Historical verification

Production rebuilds do not import V3 logic from the current development worktree.
They extract the frozen `implementation/source_tree.zip` and launch a fresh Python
process whose `PYTHONPATH` points to that extracted source tree. This means a later
legitimate change to `dev` does not invalidate the ability to verify a historical V3
epoch.

The deterministic environment contract includes Python/package versions, the Qlib
training contract, PIT contract, Git tree and dependency lock hashes. Host/platform
metadata is recorded separately and is not part of the deterministic hash.

## Offline worktree rule

The V3 source code must be validated and committed **locally** before any real freeze
is materialized. The materializer freezes the committed Git tree; it must never
freeze uncommitted implementation bytes.

When Python runs inside Docker against a Windows linked worktree, a host-created
`git archive` may be supplied through:

- `--source-tree-archive`
- `--git-commit-sha`
- `--git-tree-sha`

The core verifies every critical V3/V2 implementation file and every lockfile against
that archive before training.

## Expected branch-local flow

```text
apply complete files
    -> focused compile/tests/static audits
    -> checkpoint source candidate
    -> local git commit only
    -> create host git archive from committed HEAD
    -> materialize V3 freeze in isolated Qlib environment
    -> two frozen-source process rebuilds
    -> external checkpoint verification
    -> verify-freeze
    -> activate-freeze
    -> future prospective_start_utc
```

No push or PR is part of this branch-local flow.

## Transactional publication and derived reporting

The certified epoch is authoritative only after the complete staging tree and external
checkpoint pass integrity validation, the staging directory is atomically promoted to
the immutable epoch directory, and the published epoch passes the same integrity
validation.  Any failure before that publication boundary removes the temporary staging
directory on a best-effort basis without replacing the original exception.

The JSON under `data/reports/qlib_v3/` is derived observability, not part of the
scientific freeze identity.  A report-write failure after a certified epoch has already
been published returns a warning with `v3_freeze_reproducible=true` and
`report_write_performed=false`; it does not invalidate or recreate the immutable epoch.
Recovery of derived reporting must use a reporting/reconciliation operation rather than
rerunning materialization over an existing epoch.

Activation remains separate.  A newly certified freeze has
`prospective_start_utc=null` until the explicit activation operation creates a future
boundary.
