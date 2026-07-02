"""Deterministic dataset manifest for AI training candidates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from smartcrypto.learning.paper_autolearning.outcome_schema import SAFETY_FLAGS, utc_now_iso

DATASET_MANIFEST_SCHEMA_VERSION = "ai_unified_dataset_manifest_v1"


def build_dataset_manifest(
    frame: pd.DataFrame,
    *,
    selected_dataset_path: Path,
    source_paths: list[Path],
    feature_contract_hash: str,
    label_columns: list[str],
) -> dict[str, Any]:
    source_hashes = {str(path): file_sha256(path) for path in source_paths if path.exists() and path.is_file()}
    dtype_map = {str(column): str(frame[column].dtype) for column in frame.columns}
    null_counts = {str(column): int(frame[column].isna().sum()) for column in frame.columns}
    dataset_hash = frame_hash(frame)
    label_distribution = build_label_distribution(frame, label_columns)
    min_ts, max_ts = timestamp_bounds(frame)
    symbols = values_for_first_existing(frame, ("symbol_norm", "symbol", "moeda"))
    sides = values_for_first_existing(frame, ("side", "position_side", "fechar_side"))
    validation_errors = validate_dataset_manifest(
        frame=frame,
        label_columns=label_columns,
        feature_contract_hash=feature_contract_hash,
        dataset_hash=dataset_hash,
    )
    manifest: dict[str, Any] = {
        "schema_version": DATASET_MANIFEST_SCHEMA_VERSION,
        "dataset_id": f"dataset_{dataset_hash[:16]}",
        "dataset_hash": dataset_hash,
        "generated_at_utc": utc_now_iso(),
        "source_paths": [str(path) for path in source_paths],
        "source_hashes": source_hashes,
        "source_row_counts": {str(path): row_count(path) for path in source_paths},
        "source_column_counts": {str(path): column_count(path) for path in source_paths},
        "selected_training_dataset": str(selected_dataset_path),
        "selected_training_dataset_rows": int(len(frame)),
        "selected_training_dataset_columns": int(len(frame.columns)),
        "dataset_lineage": {
            "selected_dataset_path": str(selected_dataset_path),
            "lineage_type": "paper_feedback_training_candidate",
            "read_only_sources": True,
            "training_performed": False,
        },
        "feature_contract_hash": feature_contract_hash,
        "row_count": int(len(frame)),
        "column_count": int(len(frame.columns)),
        "null_counts": null_counts,
        "dtype_map": dtype_map,
        "min_timestamp_utc": min_ts,
        "max_timestamp_utc": max_ts,
        "symbol_count": len(symbols),
        "symbols": symbols,
        "side_count": len(sides),
        "sides": sides,
        "label_distribution": label_distribution,
        "safety_flags": safety_flags(),
        "validation_status": "blocked" if validation_errors else "ok",
        "validation_errors": validation_errors,
    }
    return manifest


def validate_dataset_manifest(
    *,
    frame: pd.DataFrame,
    label_columns: list[str],
    feature_contract_hash: str,
    dataset_hash: str,
) -> list[str]:
    errors: list[str] = []
    if frame.empty:
        errors.append("selected_dataset_empty")
    if not label_columns:
        errors.append("missing_valid_label_columns")
    if not feature_contract_hash:
        errors.append("missing_feature_contract_hash")
    if not dataset_hash:
        errors.append("missing_dataset_hash")
    return sorted(set(errors))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def frame_hash(frame: pd.DataFrame) -> str:
    payload = {
        "columns": [str(column) for column in frame.columns],
        "dtypes": {str(column): str(frame[column].dtype) for column in frame.columns},
        "records": records_for_hash(frame),
    }
    return stable_hash(payload)


def records_for_hash(frame: pd.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in frame.to_dict(orient="records"):
        records.append({str(key): json_safe(value) for key, value in row.items()})
    return records


def row_count(path: Path) -> int:
    try:
        return int(len(read_frame(path)))
    except (OSError, ValueError, ImportError, json.JSONDecodeError):
        return 0


def column_count(path: Path) -> int:
    try:
        return int(len(read_frame(path).columns))
    except (OSError, ValueError, ImportError, json.JSONDecodeError):
        return 0


def read_frame(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, list):
            return pd.DataFrame(payload)
        if isinstance(payload, Mapping):
            for key in ("rows", "data", "events"):
                value = payload.get(key)
                if isinstance(value, list):
                    return pd.DataFrame(value)
            return pd.DataFrame([payload])
    raise ValueError(f"unsupported_dataset_source:{path.suffix}")


def build_label_distribution(frame: pd.DataFrame, label_columns: list[str]) -> dict[str, dict[str, int]]:
    distribution: dict[str, dict[str, int]] = {}
    for column in label_columns:
        if column not in frame.columns:
            continue
        counts = frame[column].astype("string").fillna("<NA>").value_counts(dropna=False).sort_index()
        distribution[column] = {str(key): int(value) for key, value in counts.items()}
    return distribution


def timestamp_bounds(frame: pd.DataFrame) -> tuple[str | None, str | None]:
    for column in ("open_time_utc", "open_time", "timestamp", "date", "horario_abertura"):
        if column not in frame.columns:
            continue
        values = pd.to_datetime(frame[column], utc=True, errors="coerce").dropna()
        if values.empty:
            continue
        return values.min().isoformat(), values.max().isoformat()
    return None, None


def values_for_first_existing(frame: pd.DataFrame, candidates: tuple[str, ...]) -> list[str]:
    for column in candidates:
        if column not in frame.columns:
            continue
        values = sorted(
            {
                str(value)
                for value in frame[column].dropna().tolist()
                if str(value).strip()
            }
        )
        return values
    return []


def safety_flags() -> dict[str, bool]:
    return {
        **SAFETY_FLAGS,
        "live_trading_enabled": False,
        "training_requested": False,
        "qlib_training_performed": False,
        "ai_shadow_training_performed": False,
        "registry_write_performed": False,
    }


def stable_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=json_safe).encode("utf-8")
    ).hexdigest()


def json_safe(value: Any) -> Any:
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value
