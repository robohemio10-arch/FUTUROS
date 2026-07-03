# Paper Auto-train Feedback Loop V1

## Objective

This branch adds a research-only orchestrator for the canonical paper auto-training chain:

closed paper trades -> feedback/master consolidation -> training microbatch -> FeatureContract/DatasetManifest -> financial TargetStore -> WalkForward anti-leakage -> Qlib institutional ranking challenger -> IA Shadow quality veto challenger -> consolidated paper auto-train report.

The loop consolidates evidence and can invoke existing research-only trainers by explicit flags. It does not promote models, update runtime, write an active registry, change risk, change Freqtrade, access private exchange, or send orders.

## Sources Of Truth

The loop reads existing canonical reports from `data/reports/`:

- `paper_feedback_master_consolidation_preview_v1.json`
- `paper_autolearning_foundation_summary.json`
- `ai_unified_feature_contract_v1.json`
- `ai_unified_dataset_manifest_v1.json`
- `financial_label_target_store_v1.json`
- `walkforward_anti_leakage_split_engine_v1.json`
- Qlib backend/trainer reports when present
- IA Shadow quality veto reports when present

The dashboard is not a source of truth. Temporary CSV files are not a source of truth.

## Default Mode

Default mode is no-write and no-train:

```powershell
python .\scripts\build_paper_autotrain_feedback_loop_v1.py --project-root . --json
```

This mode reads JSON evidence only and does not invoke Qlib or IA Shadow training.

## Training Flags

Qlib research-only training:

```powershell
python .\scripts\build_paper_autotrain_feedback_loop_v1.py --project-root . --run-qlib-train --json
```

IA Shadow quality veto research-only training:

```powershell
python .\scripts\build_paper_autotrain_feedback_loop_v1.py --project-root . --run-ai-shadow-train --json
```

Both trainers are invoked through existing Python module contracts, not shell commands. The loop never passes artifact, registry, promotion, or runtime update flags.

## Report Writing

Reports are written only with `--write-report` or `--write`:

```powershell
python .\scripts\build_paper_autotrain_feedback_loop_v1.py --project-root . --run-qlib-train --write-report --json
```

Allowed outputs:

- `data/reports/paper_autotrain_feedback_loop_v1.json`
- `data/reports/paper_autotrain_feedback_loop_v1.md`

These are runtime reports and must not be versioned.

## Decisions

- `BLOCKED`: required evidence is missing or a safety violation is detected.
- `MANTER_EM_RESEARCH`: research loop ran or evidence exists, but no operational promotion is allowed.

The loop never returns or authorizes `PROMOTE`, `ACTIVE`, `LIVE_READY`, `PAPER_READY`, or any runtime veto activation.

## Safety Guarantees

All outputs preserve:

- `paper_only=true`
- `shadow_only=true`
- `operational_authority=false`
- `live_release_allowed=false`
- `canary_release_allowed=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `sends_orders=false`
- `exchange_private_access=false`
- `changes_risk=false`
- `model_promotion_requested=false`
- `model_promotion_performed=false`
- `registry_write_requested=false`
- `registry_write_performed=false`
- `active_model_changed=false`
- `qlib_runtime_updated=false`
- `ai_shadow_runtime_updated=false`
- `writes_runtime=false`
- `writes_sqlite=false`

## Limitations

This branch does not provision dependencies, rebuild upstream datasets, import trades, or resolve model performance. It only orchestrates existing contracts and emits consolidated research evidence.
