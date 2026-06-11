from __future__ import annotations

from smartcrypto.dashboard.components.dataset_pipeline import (
    build_dataset_pipeline_unknown_state,
    extract_dataset_pipeline_status,
)


def test_missing_dataset_pipeline_is_optional_unknown() -> None:
    assert build_dataset_pipeline_unknown_state()["status"] == "MISSING_OPTIONAL"
    assert extract_dataset_pipeline_status({})["status"] == "MISSING_OPTIONAL"


def test_dataset_pipeline_exposes_counts_and_validation_errors() -> None:
    state = extract_dataset_pipeline_status(
        {"sections": {"pipeline": {
            "trades_master_rows": 2864,
            "quality_gated_rows": 2631,
            "sqlite_missing_count": 0,
            "sqlite_extra_count": 0,
            "validation_errors": [],
        }}}
    )
    assert state["trades_master_rows"] == 2864
    assert state["quality_gated_rows"] == 2631
    assert state["sqlite_missing_count"] == 0
    assert state["sqlite_extra_count"] == 0
    assert state["validation_errors"] == []


def test_dataset_pipeline_component_has_no_mutating_operations() -> None:
    from smartcrypto.dashboard.components import dataset_pipeline

    text = open(dataset_pipeline.__file__, encoding="utf-8").read().lower()
    for token in ("subprocess", "write_text", "to_parquet", "to_excel", "sqlite3", "import_official"):
        assert token not in text
