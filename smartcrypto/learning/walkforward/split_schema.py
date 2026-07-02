"""Schema constants for walk-forward anti-leakage split evidence."""

from __future__ import annotations

from pathlib import Path

SCHEMA_VERSION = "walkforward_anti_leakage_split_engine_v1"

DEFAULT_FEATURE_CONTRACT_JSON = Path("data/reports/ai_unified_feature_contract_v1.json")
DEFAULT_DATASET_MANIFEST_JSON = Path("data/reports/ai_unified_dataset_manifest_v1.json")
DEFAULT_TARGET_STORE_JSON = Path("data/reports/financial_label_target_store_v1.json")
DEFAULT_TARGET_STORE_SUMMARY_JSON = Path("data/reports/financial_label_target_store_summary_v1.json")
DEFAULT_OUTPUT_JSON = Path("data/reports/walkforward_anti_leakage_split_engine_v1.json")
DEFAULT_OUTPUT_MD = Path("data/reports/walkforward_anti_leakage_split_engine_v1.md")
DEFAULT_BASELINE_JSON = Path("data/reports/walkforward_baseline_summary_v1.json")
DEFAULT_BASELINE_MD = Path("data/reports/walkforward_baseline_summary_v1.md")
DEFAULT_MICROBATCH_DIR = Path("data/feedback/training_microbatches")
DEFAULT_OUTCOME_EVENTS = Path("data/feedback/outcome_events.parquet")

DEFAULT_BASELINE_SEED = 1337
MINIMUM_EMBARGO_SECONDS = 86_400

SAFETY_FALSE_FIELDS = {
    "training_requested": False,
    "qlib_training_performed": False,
    "ai_shadow_training_performed": False,
    "registry_write_performed": False,
    "model_promotion_performed": False,
    "active_model_changed": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "changes_risk": False,
    "writes_runtime": False,
    "writes_sqlite": False,
    "writes_parquet": False,
}
