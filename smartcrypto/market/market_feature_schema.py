from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


LOOKAHEAD_PREFIXES = ("future_ret_",)
DEFAULT_LABEL_KEYS = ("symbol", "pair", "tf", "ts", "ts_ms")


def lookahead_columns(frame: pd.DataFrame) -> list[str]:
    return [
        str(column)
        for column in frame.columns
        if any(str(column).startswith(prefix) for prefix in LOOKAHEAD_PREFIXES)
    ]


def sanitize_operational_market_features(
    frame: pd.DataFrame,
    *,
    labels_output_path: str | Path | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Remove lookahead labels from operational market feature artifacts."""
    removed = lookahead_columns(frame)
    sanitized = frame.drop(columns=removed).copy() if removed else frame.copy()
    label_path = None
    if labels_output_path is not None and removed:
        label_path = write_market_feature_labels(frame, labels_output_path)
    return sanitized, operational_schema_report(
        frame=sanitized,
        lookahead_columns_removed=removed,
        labels_output_path=label_path,
    )


def write_operational_market_features(
    frame: pd.DataFrame,
    output_path: str | Path,
    *,
    labels_output_path: str | Path | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Atomically write market features after enforcing the no-lookahead contract."""
    sanitized, report = sanitize_operational_market_features(
        frame,
        labels_output_path=labels_output_path,
    )
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    sanitized.to_parquet(tmp, index=False)
    tmp.replace(target)
    return sanitized, report


def write_market_feature_labels(frame: pd.DataFrame, output_path: str | Path) -> str:
    label_columns = lookahead_columns(frame)
    if not label_columns:
        return str(output_path)
    keys = [column for column in DEFAULT_LABEL_KEYS if column in frame.columns]
    labels = frame[keys + label_columns].copy()
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    labels.to_parquet(tmp, index=False)
    tmp.replace(target)
    return str(target)


def operational_schema_report(
    *,
    frame: pd.DataFrame,
    lookahead_columns_removed: list[str] | None = None,
    labels_output_path: str | None = None,
) -> dict[str, Any]:
    current_lookahead = lookahead_columns(frame)
    removed = sorted(lookahead_columns_removed or [])
    return {
        "output_schema_status": "ok" if not current_lookahead else "blocked",
        "operational_feature_schema_ok": not current_lookahead,
        "lookahead_columns": current_lookahead,
        "lookahead_columns_count": len(current_lookahead),
        "lookahead_columns_removed": removed,
        "lookahead_columns_removed_count": len(removed),
        "labels_output_path": labels_output_path,
    }
