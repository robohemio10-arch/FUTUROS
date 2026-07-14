"""Quarantined legacy Trader Master writer implementation.

Read-only tabular helpers moved to :mod:`smartcrypto.data.trade_file_readonly`.
The writer remains inventory evidence only; its import orchestration is disabled
before any filesystem access.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from smartcrypto.data.trade_file_readonly import (
    CANONICAL_COLUMNS,
    COLUMN_ALIASES,
    RECOMMENDED_COLUMNS,
    REQUIRED_COLUMNS,
    SUPPORTED_EXTENSIONS,
    build_alias_lookup,
    build_dedup_key,
    clean_trade_frame,
    normalize_column_name,
    normalize_columns,
    now_utc_iso,
    read_trade_file,
    validate_trade_frame,
)


class LegacyMasterImportDisabledError(RuntimeError):
    """Raised when a caller attempts the quarantined legacy import path."""


def read_master(master_parquet_path: Path, master_xlsx_path: Path) -> pd.DataFrame:
    """Legacy reader retained only for historical inventory; do not call."""

    if master_parquet_path.exists():
        return pd.read_parquet(master_parquet_path)
    if master_xlsx_path.exists():
        return clean_trade_frame(pd.read_excel(master_xlsx_path), source_file=None)
    return pd.DataFrame(columns=CANONICAL_COLUMNS + ["source_file", "imported_at"])


def write_master(
    frame: pd.DataFrame,
    master_xlsx_path: Path,
    master_parquet_path: Path,
    compatibility_xlsx_path: Path,
) -> None:
    """Legacy writer retained as quarantined evidence without callsites."""

    master_xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    master_parquet_path.parent.mkdir(parents=True, exist_ok=True)
    compatibility_xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    output = frame.copy()
    output.to_parquet(master_parquet_path, index=False)
    output.to_excel(master_xlsx_path, index=False)
    output[CANONICAL_COLUMNS].to_excel(compatibility_xlsx_path, index=False)


def import_trades_incrementally(
    inbox_dir: Path,
    master_xlsx_path: Path,
    master_parquet_path: Path,
    compatibility_xlsx_path: Path,
    processed_dir: Path,
    report_path: Path,
    archive: bool = True,
) -> dict[str, object]:
    """Block the legacy import before any read, directory creation, or write."""

    del (
        inbox_dir,
        master_xlsx_path,
        master_parquet_path,
        compatibility_xlsx_path,
        processed_dir,
        report_path,
        archive,
    )
    raise LegacyMasterImportDisabledError(
        "legacy Trader Master import disabled by research-only governance"
    )


__all__ = [
    "CANONICAL_COLUMNS",
    "COLUMN_ALIASES",
    "LegacyMasterImportDisabledError",
    "RECOMMENDED_COLUMNS",
    "REQUIRED_COLUMNS",
    "SUPPORTED_EXTENSIONS",
    "build_alias_lookup",
    "build_dedup_key",
    "clean_trade_frame",
    "import_trades_incrementally",
    "normalize_column_name",
    "normalize_columns",
    "now_utc_iso",
    "read_master",
    "read_trade_file",
    "validate_trade_frame",
    "write_master",
]
