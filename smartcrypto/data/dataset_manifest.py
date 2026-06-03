from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


DEFAULT_MANIFEST_PATH = Path("data/reports/dataset_manifest.json")
MANIFEST_VERSION = "1.0"


class DatasetManifestError(ValueError):
    pass


def build_dataset_manifest(
    *,
    inputs: list[str | Path],
    output_path: str | Path | None = DEFAULT_MANIFEST_PATH,
    dataset_role: str = "dataset",
    timestamp_column: str | None = None,
    strict: bool = False,
    safety_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    output = Path(output_path) if output_path is not None else None
    safe = safety_payload(safety_overrides)
    validation_errors: list[str] = []
    warnings: list[str] = []
    if unsafe := unsafe_safety_flags(safe):
        validation_errors.extend(f"unsafe_safety_flag:{item}" for item in unsafe)
    files = [
        file_manifest(
            Path(path),
            dataset_role=dataset_role,
            timestamp_column=timestamp_column,
            strict=strict,
        )
        for path in inputs
    ]
    for item in files:
        if item.get("validation_errors"):
            validation_errors.extend(item["validation_errors"])
        if item.get("warnings"):
            warnings.extend(item["warnings"])
    if strict and any(not item["exists"] for item in files):
        validation_errors.append("missing_required_file")
    status = "blocked" if validation_errors else "warning" if warnings else "ok"
    report = {
        "status": status,
        "reason": "ok" if status == "ok" else ";".join(sorted(set(validation_errors or warnings))),
        "generated_at_utc": utc_timestamp(),
        "manifest_version": MANIFEST_VERSION,
        "files": files,
        "global_schema_summary": global_schema_summary(files),
        "validation_errors": sorted(set(validation_errors)),
        "warnings": sorted(set(warnings)),
        **safe,
    }
    write_json_if_requested(report, output)
    return report


def file_manifest(
    path: Path,
    *,
    dataset_role: str,
    timestamp_column: str | None,
    strict: bool,
) -> dict[str, Any]:
    exists = path.exists()
    payload: dict[str, Any] = {
        "path": str(path),
        "exists": bool(exists),
        "file_size_bytes": None,
        "sha256": None,
        "modified_at_utc": None,
        "detected_format": path.suffix.lower().lstrip("."),
        "rows": 0,
        "columns": 0,
        "column_names": [],
        "min_timestamp": None,
        "max_timestamp": None,
        "min_timestamp_utc": None,
        "max_timestamp_utc": None,
        "schema_hash": None,
        "dataset_role": dataset_role,
        "manifest_version": MANIFEST_VERSION,
        "validation_errors": [],
        "warnings": [],
    }
    if not exists:
        payload["validation_errors"].append(f"missing_file:{path}")
        return payload
    try:
        payload["file_size_bytes"] = int(path.stat().st_size)
        payload["sha256"] = file_sha256(path)
        payload["modified_at_utc"] = datetime.fromtimestamp(
            path.stat().st_mtime,
            tz=timezone.utc,
        ).isoformat().replace("+00:00", "Z")
    except OSError as exc:
        payload["validation_errors"].append(f"hash_or_stat_failed:{exc}")
        return payload
    try:
        frame = read_table(path)
    except Exception as exc:
        payload["validation_errors"].append(f"schema_not_readable:{exc}")
        return payload
    payload["rows"] = int(len(frame))
    payload["columns"] = int(len(frame.columns))
    payload["column_names"] = [str(column) for column in frame.columns]
    payload["schema_hash"] = schema_hash(frame)
    if frame.empty:
        payload["validation_errors"].append("empty_file")
    time_column = resolve_timestamp_column(frame, timestamp_column)
    if time_column:
        timestamps = pd.to_datetime(frame[time_column], utc=True, errors="coerce")
        valid = timestamps.dropna()
        if not valid.empty:
            payload["min_timestamp"] = to_iso(valid.min())
            payload["max_timestamp"] = to_iso(valid.max())
            payload["min_timestamp_utc"] = payload["min_timestamp"]
            payload["max_timestamp_utc"] = payload["max_timestamp"]
    elif strict:
        payload["warnings"].append("timestamp_column_not_found")
    return payload


def global_schema_summary(files: list[dict[str, Any]]) -> dict[str, Any]:
    schema_hashes = sorted({item["schema_hash"] for item in files if item.get("schema_hash")})
    return {
        "files_count": int(len(files)),
        "existing_files_count": int(sum(1 for item in files if item.get("exists"))),
        "schema_hashes": schema_hashes,
        "unique_schema_hashes": int(len(schema_hashes)),
        "total_rows": int(sum(int(item.get("rows") or 0) for item in files)),
    }


def read_table(path: str | Path) -> pd.DataFrame:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(file_path)
    if suffix == ".csv":
        return pd.read_csv(file_path)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(file_path)
    if suffix == ".jsonl":
        rows = []
        with file_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))
        return pd.DataFrame(rows)
    if suffix == ".json":
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return pd.DataFrame(payload)
        if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
            return pd.DataFrame(payload["rows"])
        return pd.DataFrame([payload])
    raise DatasetManifestError(f"unsupported_input_format:{suffix}")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def schema_hash(frame: pd.DataFrame) -> str:
    payload = {
        "columns": [str(column) for column in frame.columns],
        "dtypes": {str(column): str(frame[column].dtype) for column in frame.columns},
    }
    material = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def resolve_timestamp_column(frame: pd.DataFrame, timestamp_column: str | None) -> str | None:
    if timestamp_column and timestamp_column in frame.columns:
        return timestamp_column
    candidates = (
        "timestamp",
        "open_ts",
        "open_time_utc",
        "opened_at",
        "date",
        "datetime",
        "horario_abertura",
    )
    return next((column for column in candidates if column in frame.columns), None)


def safety_payload(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "paper_only": True,
        "shadow_only": True,
        "runtime_mode": "paper",
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
    }
    if overrides:
        payload.update(overrides)
    return payload


def unsafe_safety_flags(payload: dict[str, Any]) -> list[str]:
    unsafe = []
    if payload.get("paper_only") is not True:
        unsafe.append("paper_only")
    if payload.get("shadow_only") is not True:
        unsafe.append("shadow_only")
    if payload.get("runtime_mode") != "paper":
        unsafe.append("runtime_mode")
    for key in (
        "live_trading_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_risk",
    ):
        if payload.get(key) is True:
            unsafe.append(key)
    return unsafe


def write_json_if_requested(payload: dict[str, Any], path: Path | None) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True, default=safe_json),
        encoding="utf-8",
    )


def safe_json(value: Any) -> Any:
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    return str(value)


def to_iso(value: pd.Timestamp) -> str:
    return pd.Timestamp(value).isoformat().replace("+00:00", "Z")


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
