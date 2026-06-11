from __future__ import annotations

import csv
import importlib
import json
from pathlib import Path
from typing import Any

from smartcrypto.ops.dashboard_snapshots.contracts import (
    DashboardLoadResult,
    DashboardSectionStatus,
    SourceKind,
)


def load_dashboard_file(
    path: str | Path,
    source_kind: SourceKind | str = SourceKind.OPTIONAL_EXISTING_SOURCE,
) -> DashboardLoadResult:
    source_kind = _normalize_source_kind(source_kind)
    target = Path(path)
    if not target.is_file():
        return DashboardLoadResult(
            exists=False,
            status=_missing_status(source_kind),
            path=str(target),
            error="file_not_found",
            source_kind=source_kind,
        )

    suffix = target.suffix.lower()
    try:
        if suffix == ".json":
            data = json.loads(target.read_text(encoding="utf-8-sig"))
        elif suffix == ".jsonl":
            data = _load_jsonl(target)
        elif suffix == ".parquet":
            data = _load_parquet(target)
        elif suffix == ".csv":
            data = _load_csv(target)
        elif suffix in {".sqlite", ".sqlite3", ".db"}:
            data = {
                "path": str(target),
                "size_bytes": target.stat().st_size,
                "read_only": True,
                "database_opened": False,
            }
        else:
            return DashboardLoadResult(
                exists=True,
                status=DashboardSectionStatus.ERROR,
                path=str(target),
                error=f"unsupported_file_type:{suffix or 'none'}",
                source_kind=source_kind,
            )
    except Exception as exc:
        return DashboardLoadResult(
            exists=True,
            status=DashboardSectionStatus.ERROR,
            path=str(target),
            error=f"{type(exc).__name__}:{exc}",
            source_kind=source_kind,
        )

    return DashboardLoadResult(
        exists=True,
        status=DashboardSectionStatus.OK,
        path=str(target),
        data=data,
        source_kind=source_kind,
    )


def load_json_file(
    path: str | Path,
    source_kind: SourceKind = SourceKind.OPTIONAL_EXISTING_SOURCE,
) -> DashboardLoadResult:
    return _load_with_expected_suffix(path, source_kind, ".json")


def load_jsonl_file(
    path: str | Path,
    source_kind: SourceKind = SourceKind.OPTIONAL_EXISTING_SOURCE,
) -> DashboardLoadResult:
    return _load_with_expected_suffix(path, source_kind, ".jsonl")


def load_parquet_file(
    path: str | Path,
    source_kind: SourceKind = SourceKind.OPTIONAL_EXISTING_SOURCE,
) -> DashboardLoadResult:
    return _load_with_expected_suffix(path, source_kind, ".parquet")


def _load_with_expected_suffix(
    path: str | Path,
    source_kind: SourceKind,
    expected_suffix: str,
) -> DashboardLoadResult:
    target = Path(path)
    if target.suffix.lower() != expected_suffix and target.is_file():
        return DashboardLoadResult(
            exists=True,
            status=DashboardSectionStatus.ERROR,
            path=str(target),
            error=f"expected_{expected_suffix.lstrip('.')}_file",
            source_kind=source_kind,
        )
    return load_dashboard_file(target, source_kind)


def _load_jsonl(path: Path) -> list[Any]:
    rows: list[Any] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                rows.append(json.loads(text))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid_jsonl_line:{line_number}:{exc.msg}") from exc
    return rows


def _load_parquet(path: Path) -> Any:
    importlib.import_module("pyarrow")
    pandas = importlib.import_module("pandas")
    return pandas.read_parquet(path)


def _load_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _missing_status(source_kind: SourceKind) -> DashboardSectionStatus:
    if source_kind is SourceKind.REQUIRED_EXISTING_SOURCE:
        return DashboardSectionStatus.MISSING_REQUIRED
    if source_kind is SourceKind.OPTIONAL_EXISTING_SOURCE:
        return DashboardSectionStatus.MISSING_OPTIONAL
    return DashboardSectionStatus.UNKNOWN


def _normalize_source_kind(value: SourceKind | str) -> SourceKind:
    if isinstance(value, SourceKind):
        return value
    try:
        return SourceKind(str(value))
    except ValueError:
        return SourceKind.FUTURE_SOURCE


load_file_readonly = load_dashboard_file
