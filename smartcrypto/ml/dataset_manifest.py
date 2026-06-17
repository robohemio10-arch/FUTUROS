from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from smartcrypto.ml.unified_feature_contract import (
    SAFETY_FLAGS,
    always_blocked_columns,
    duplicate_key_count,
    forbidden_feature_columns,
    read_table,
    resolve_identity_key_columns,
    resolve_timestamp_column,
    safe_json,
    select_feature_columns,
    stable_hash,
    unsafe_safety_flags,
)

Status = Literal["ok", "blocked"]
MANIFEST_ID = "ai_unified_dataset_manifest"
MANIFEST_VERSION = "v1"


class UnifiedDatasetManifestError(ValueError):
    """Raised when an AI dataset manifest cannot be built."""


@dataclass(frozen=True)
class DatasetFileManifest:
    path: str
    role: str
    exists: bool
    rows: int = 0
    columns: int = 0
    file_size_bytes: int | None = None
    file_sha256: str | None = None
    schema_hash: str | None = None
    dataset_hash: str | None = None
    feature_columns: tuple[str, ...] = ()
    forbidden_feature_columns: tuple[str, ...] = ()
    timestamp_column: str | None = None
    min_timestamp_utc: str | None = None
    max_timestamp_utc: str | None = None
    duplicate_timestamp_count: int = 0
    validation_errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class UnifiedDatasetManifest:
    status: Status
    reason: str
    manifest_id: str = MANIFEST_ID
    manifest_version: str = MANIFEST_VERSION
    generated_at_utc: str = field(default_factory=lambda: utc_timestamp())
    files: tuple[DatasetFileManifest, ...] = ()
    dataset_hash: str | None = None
    schema_hash: str | None = None
    validation_errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    paper_only: bool = True
    shadow_only: bool = True
    runtime_mode: str = "paper"
    live_trading_enabled: bool = False
    order_submission_enabled: bool = False
    real_order_submission_enabled: bool = False
    exchange_private_access: bool = False
    sends_orders: bool = False
    changes_risk: bool = False
    changes_model: bool = False
    changes_training_dataset: bool = False
    writes_trades_master: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["files"] = [item.to_dict() for item in self.files]
        return payload


def build_dataset_file_manifest(
    path: str | Path,
    *,
    role: str,
    strict: bool = True,
) -> DatasetFileManifest:
    file_path = Path(path)
    errors: list[str] = []
    warnings: list[str] = []
    if not file_path.exists():
        errors.append(f"missing_dataset:{file_path}")
        return DatasetFileManifest(
            path=str(file_path),
            role=role,
            exists=False,
            validation_errors=tuple(errors),
        )

    try:
        file_size = int(file_path.stat().st_size)
        file_digest = file_sha256(file_path)
        frame = read_table(file_path)
    except (OSError, ValueError, TypeError, ImportError, EOFError) as exc:
        return DatasetFileManifest(
            path=str(file_path),
            role=role,
            exists=True,
            validation_errors=(f"dataset_not_readable:{type(exc).__name__}:{exc}",),
        )

    if frame.empty:
        errors.append("dataset_empty")
    if duplicated := duplicated_columns(frame):
        errors.append(f"duplicate_columns:{duplicated}")

    feature_columns = tuple(select_feature_columns(frame, include_non_numeric=True))
    blocked_source_columns = tuple(always_blocked_columns(tuple(str(column) for column in frame.columns)))
    forbidden = tuple(forbidden_feature_columns(feature_columns))
    if blocked_source_columns:
        errors.append(f"blocked_source_columns:{list(blocked_source_columns)}")
    if forbidden:
        errors.append(f"forbidden_feature_columns:{list(forbidden)}")

    timestamp_column = resolve_timestamp_column(frame)
    min_timestamp: str | None = None
    max_timestamp: str | None = None
    duplicate_timestamp_count = 0
    if timestamp_column:
        timestamps = pd.to_datetime(frame[timestamp_column], utc=True, errors="coerce")
        valid = timestamps.dropna()
        if not valid.empty:
            min_timestamp = to_iso(valid.min())
            max_timestamp = to_iso(valid.max())
            identity_key = resolve_identity_key_columns(frame)
            duplicate_timestamp_count = duplicate_key_count(frame, identity_key) if identity_key else 0
            if duplicate_timestamp_count:
                if len(identity_key) == 1 and identity_key[0] == timestamp_column:
                    errors.append(f"duplicate_timestamp:{timestamp_column}:{duplicate_timestamp_count}")
                else:
                    errors.append(f"duplicate_identity_key:{list(identity_key)}:{duplicate_timestamp_count}")
    elif strict:
        warnings.append("timestamp_column_not_found")

    return DatasetFileManifest(
        path=str(file_path),
        role=role,
        exists=True,
        rows=int(len(frame)),
        columns=int(len(frame.columns)),
        file_size_bytes=file_size,
        file_sha256=file_digest,
        schema_hash=dataframe_schema_hash(frame),
        dataset_hash=dataframe_dataset_hash(frame),
        feature_columns=feature_columns,
        forbidden_feature_columns=forbidden,
        timestamp_column=timestamp_column,
        min_timestamp_utc=min_timestamp,
        max_timestamp_utc=max_timestamp,
        duplicate_timestamp_count=duplicate_timestamp_count,
        validation_errors=tuple(errors),
        warnings=tuple(warnings),
    )


def build_unified_dataset_manifest(
    paths_by_role: dict[str, str | Path],
    *,
    strict: bool = True,
    safety_overrides: dict[str, Any] | None = None,
) -> UnifiedDatasetManifest:
    flags = {**SAFETY_FLAGS, **(safety_overrides or {})}
    errors: list[str] = []
    warnings: list[str] = []
    if unsafe := unsafe_safety_flags(flags):
        errors.extend(f"unsafe_safety_flag:{flag}" for flag in unsafe)

    files = tuple(
        build_dataset_file_manifest(path, role=role, strict=strict)
        for role, path in paths_by_role.items()
    )
    for item in files:
        errors.extend(item.validation_errors)
        warnings.extend(item.warnings)

    if strict and not files:
        errors.append("no_datasets_configured")

    dataset_hash = stable_hash(
        {
            item.role: {
                "rows": item.rows,
                "columns": item.columns,
                "schema_hash": item.schema_hash,
                "dataset_hash": item.dataset_hash,
                "feature_columns": list(item.feature_columns),
            }
            for item in files
        }
    )
    schema_hash = stable_hash(
        {
            item.role: {
                "schema_hash": item.schema_hash,
                "feature_columns": list(item.feature_columns),
            }
            for item in files
        }
    )
    status: Status = "blocked" if errors else "ok"
    return UnifiedDatasetManifest(
        status=status,
        reason="ok" if status == "ok" else ";".join(sorted(set(errors))),
        files=files,
        dataset_hash=dataset_hash,
        schema_hash=schema_hash,
        validation_errors=tuple(sorted(set(errors))),
        warnings=tuple(sorted(set(warnings))),
        **flags,
    )


def write_manifest(manifest: UnifiedDatasetManifest, path: str | Path) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False, sort_keys=True, default=safe_json),
        encoding="utf-8",
    )


def dataframe_schema_hash(frame: pd.DataFrame) -> str:
    return stable_hash(
        {
            "columns": [str(column) for column in frame.columns],
            "dtypes": {str(column): str(frame[column].dtype) for column in frame.columns},
        }
    )


def dataframe_dataset_hash(frame: pd.DataFrame) -> str:
    normalized = frame.copy()
    normalized.columns = [str(column) for column in normalized.columns]
    for column in normalized.columns:
        if pd.api.types.is_datetime64_any_dtype(normalized[column]):
            normalized[column] = pd.to_datetime(normalized[column], utc=True, errors="coerce").map(
                lambda value: value.isoformat() if pd.notna(value) else None
            )
    records = normalized.astype(object).where(pd.notna(normalized), None).to_dict(orient="records")
    return stable_hash({"columns": list(normalized.columns), "records": records})


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def duplicated_columns(frame: pd.DataFrame) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for column in (str(item) for item in frame.columns):
        if column in seen:
            duplicates.add(column)
        seen.add(column)
    return sorted(duplicates)


def to_iso(value: pd.Timestamp) -> str:
    return pd.Timestamp(value).isoformat().replace("+00:00", "Z")


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
