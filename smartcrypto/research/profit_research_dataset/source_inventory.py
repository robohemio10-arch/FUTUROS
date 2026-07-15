"""Deterministic source inventory helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd


def file_sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_frame_sha256(frame: pd.DataFrame) -> str:
    normalized = frame.copy()
    normalized = normalized.reindex(sorted(normalized.columns), axis=1)
    rows = []
    for row in normalized.to_dict(orient="records"):
        rows.append({key: _json_value(value) for key, value in sorted(row.items())})
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def inventory_frame(
    *,
    path: Path,
    source_type: str,
    frame: pd.DataFrame,
    read_only_status: str,
    source_hash: str | None = None,
) -> dict[str, Any]:
    timestamps = _timestamp_series(frame)
    symbols = _values(frame, ("symbol", "pair", "moeda"))
    timeframes = _values(frame, ("tf", "timeframe"))
    return {
        "logical_path": str(path),
        "source_type": source_type,
        "sha256": source_hash if source_hash is not None else file_sha256(path),
        "size_bytes": path.stat().st_size if path.exists() and path.is_file() else None,
        "min_timestamp_utc": timestamps.min().isoformat() if not timestamps.empty else None,
        "max_timestamp_utc": timestamps.max().isoformat() if not timestamps.empty else None,
        "row_count": int(len(frame)),
        "schema_observed": [str(column) for column in frame.columns],
        "symbol_coverage": symbols,
        "timeframe_coverage": timeframes,
        "source_status": "ok" if not frame.empty else "empty",
        "freshness": _freshness(timestamps),
        "read_only_status": read_only_status,
    }


def inventory_sqlite_snapshot(
    *,
    path: Path,
    frame: pd.DataFrame,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    hashes = metadata.get("snapshot_source_hashes_before", {})
    db_hash = None
    for name, item in hashes.items() if isinstance(hashes, Mapping) else ():
        if str(name).endswith((".sqlite", ".db")) and isinstance(item, Mapping):
            db_hash = item.get("sha256")
            break
    item = inventory_frame(
        path=path,
        source_type="paper_sqlite_snapshot",
        frame=frame,
        read_only_status="temporary_copy_query_only",
        source_hash=str(db_hash) if db_hash else None,
    )
    item.update(
        source_status=metadata.get("status", item["source_status"]),
        source_reason=metadata.get("reason"),
        temp_copy_used=bool(metadata.get("snapshot_temp_copy_used")),
        query_only=bool(metadata.get("snapshot_query_only")),
        source_hash_preserved=bool(metadata.get("snapshot_source_hashes_preserved")),
    )
    return item


def _timestamp_series(frame: pd.DataFrame) -> pd.Series:
    for column in ("ts", "close_time_utc", "close_date", "open_time_utc", "open_date"):
        if column in frame.columns:
            return pd.to_datetime(frame[column], utc=True, errors="coerce").dropna()
    return pd.Series(dtype="datetime64[ns, UTC]")


def _values(frame: pd.DataFrame, candidates: tuple[str, ...]) -> list[str]:
    for column in candidates:
        if column in frame.columns:
            return sorted(frame[column].dropna().astype(str).unique().tolist())
    return []


def _freshness(timestamps: pd.Series) -> dict[str, Any]:
    if timestamps.empty:
        return {"status": "unknown", "last_timestamp_utc": None}
    return {"status": "observed", "last_timestamp_utc": timestamps.max().isoformat()}


def _json_value(value: Any) -> Any:
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, float) and pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value
