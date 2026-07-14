"""Fail-closed compatibility boundary for retired paper consolidation."""

from __future__ import annotations

from pathlib import Path
from typing import Any


SCHEMA_VERSION = "paper_feedback_legacy_consolidation_disabled_v1"


def build_paper_feedback_master_consolidation_report(
    *,
    project_root: str | Path,
    source_path: str | Path | None = None,
    preview_json_path: str | Path | None = None,
    preview_markdown_path: str | Path | None = None,
    backup_root: str | Path | None = None,
    write_preview: bool = False,
    write_master: bool = False,
    **retired_options: str | Path | None,
) -> dict[str, Any]:
    """Reject legacy consolidation without reading or writing any supplied path."""

    requested_paths = {
        "project_root": str(project_root),
        "source_path": str(source_path) if source_path is not None else None,
        "preview_json_path": (
            str(preview_json_path) if preview_json_path is not None else None
        ),
        "preview_markdown_path": (
            str(preview_markdown_path) if preview_markdown_path is not None else None
        ),
        "backup_root": str(backup_root) if backup_root is not None else None,
        "retired_option_names": sorted(retired_options),
    }
    return {
        "status": "blocked",
        "reason": "legacy_dataset_consolidation_disabled",
        "decision": "LEGACY_DATASET_CONSOLIDATION_FORBIDDEN",
        "schema_version": SCHEMA_VERSION,
        "requested_paths": requested_paths,
        "write_preview_requested": bool(write_preview),
        "write_master_requested": bool(write_master),
        "write_performed": False,
        "master_write_performed": False,
        "backup_created": False,
        "import_authorized": False,
        "write_authorized": False,
        "operational_authority": False,
        "writes_parquet": False,
        "writes_xlsx": False,
        "writes_csv": False,
        "writes_sqlite": False,
        "writes_runtime": False,
        "sends_orders": False,
        "changes_risk": False,
        "exchange_private_access": False,
        "safety_flags": {
            "import_authorized": False,
            "write_authorized": False,
            "operational_authority": False,
            "writes_runtime": False,
            "sends_orders": False,
            "changes_risk": False,
            "exchange_private_access": False,
        },
    }


__all__ = ["SCHEMA_VERSION", "build_paper_feedback_master_consolidation_report"]
