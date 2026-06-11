from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .snapshot_cards import render_key_value_grid


DATASET_PIPELINE_FIELDS = (
    "latest_ocr_package", "ocr_batch_id", "ocr_staging_status", "preview_only_status",
    "post_import_audit_status", "official_import_status", "trades_master_rows",
    "trade_enriched_rows", "training_dataset_rows", "quality_gated_rows",
    "ai_shadow_sqlite_rows", "sqlite_missing_count", "sqlite_extra_count",
    "incremental_scoring_status", "last_dataset_rebuild_utc", "validation_errors",
    "blocked_reasons",
)


def build_dataset_pipeline_unknown_state() -> dict[str, Any]:
    return {
        "status": "MISSING_OPTIONAL",
        "reason": "dataset_pipeline_fields_not_available_in_snapshot",
    }


def extract_dataset_pipeline_status(snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    source = snapshot if isinstance(snapshot, Mapping) else {}
    result = {field: _find_value(source, field) for field in DATASET_PIPELINE_FIELDS}
    if not any(value is not None for value in result.values()):
        return build_dataset_pipeline_unknown_state()
    return {"status": "READ_ONLY", "reason": "snapshot_view_only", **result}


def render_dataset_ocr_training_pipeline_status(snapshot: Mapping[str, Any], *, ui: Any) -> None:
    ui.subheader("Dataset / OCR / Training Pipeline Status")
    render_key_value_grid(extract_dataset_pipeline_status(snapshot), ui=ui)


def _find_value(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        if key in value:
            return value[key]
        for child in value.values():
            found = _find_value(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_value(child, key)
            if found is not None:
                return found
    return None
