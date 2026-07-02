# Auto-learning Canonical Loop Closeout Evidence V1

This branch creates a read-only evidence pack for the canonical paper/shadow auto-learning loop.

## Objective

The closeout consolidates already generated evidence for the eight canonical stages:

1. Paper feedback foundation loop.
2. Paper feedback master consolidation.
3. Paper auto-learning scheduler evidence.
4. FeatureContract and DatasetManifest.
5. Financial TargetStore.
6. WalkForward anti-leakage split engine.
7. Qlib institutional ranking challenger.
8. AI Shadow quality veto challenger.

It proves lineage hashes, stage status, safety flags, and research-only decisions. It does not train, promote, register, deploy, or activate anything.

## Inputs

The CLI reads JSON evidence files from `data/reports/` and `PROJECT_MANIFEST_CLEAN.json`. Missing or invalid evidence blocks the closeout with `canonical_loop_decision=BLOCKED_MISSING_EVIDENCE`.

The pack validates these lineage links:

- `FeatureContract` hash used by `DatasetManifest`.
- `TargetStore` references the same FeatureContract and DatasetManifest.
- `WalkForward` references the same FeatureContract, DatasetManifest, and TargetStore.
- Qlib trainer references FeatureContract, DatasetManifest, TargetStore, and WalkForward.
- AI Shadow quality veto trainer references the same lineage and, when present, the Qlib trainer report hash.

## Outputs

Default mode is no-write. With `--write`, only report artifacts are written:

- `data/reports/autolearning_canonical_loop_closeout_evidence_v1.json`
- `data/reports/autolearning_canonical_loop_closeout_evidence_v1.md`
- `data/reports/autolearning_canonical_loop_lineage_matrix_v1.json`
- `data/reports/autolearning_canonical_loop_safety_matrix_v1.json`

These are runtime reports and must not be versioned.

## Decisions

- `BLOCKED_MISSING_EVIDENCE`: one or more required evidence files are absent or invalid.
- `BLOCKED_LINEAGE_DRIFT`: lineage hashes are inconsistent.
- `BLOCKED_SAFETY_VIOLATION`: any operational safety flag is violated.
- `CANONICAL_RESEARCH_LOOP_CLOSED`: evidence is complete, lineage is clean, and safety is preserved.

The closeout never returns `PROMOTE`, `ACTIVE`, `LIVE_READY`, `PAPER_READY`, or `VETO_ACTIVE`.

## Safety Boundaries

The closeout is research-only/read-only evidence. It does not:

- Train models.
- Promote models.
- Write an active registry.
- Change champion models.
- Activate AI Shadow runtime.
- Activate veto runtime.
- Update Qlib runtime.
- Change Freqtrade, RiskManager, or signal producer.
- Send orders.
- Access private exchange endpoints.
- Write SQLite, parquet, runtime state, or model artifacts.

`qlib_backend_unavailable` is classified as a warning for this closeout because the branch is evidence consolidation, not backend provisioning.

## Commands

```powershell
python .\scripts\build_autolearning_canonical_loop_closeout_evidence_v1.py --project-root . --json
python .\scripts\build_autolearning_canonical_loop_closeout_evidence_v1.py --project-root . --write --json
```

Validation:

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests\test_autolearning_canonical_loop_closeout_evidence_v1.py -q
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r ".\requirements-dev.lock" --progress-spinner off
```
