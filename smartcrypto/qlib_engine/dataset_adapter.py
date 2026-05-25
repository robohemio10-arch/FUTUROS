from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.qlib_engine.common import QlibEngineConfig, qlib_runtime_status, write_json


def build_qlib_market_dataset(
    *,
    market_features_path: str | Path,
    output_path: str | Path,
    metadata_path: str | Path,
    config: QlibEngineConfig,
) -> dict[str, Any]:
    source = Path(market_features_path)
    if not source.exists():
        report = {
            "status": "blocked",
            "reason": "market_features_missing",
            "market_features_path": str(source),
        }
        write_json(metadata_path, report)
        return report

    frame = pd.read_parquet(source)
    required = {"symbol", "pair", "tf", "ts", config.future_return_column, *config.feature_columns}
    missing = sorted(required.difference(frame.columns))
    if missing:
        report = {
            "status": "blocked",
            "reason": "missing_columns",
            "missing_columns": missing,
            "market_features_path": str(source),
        }
        write_json(metadata_path, report)
        return report

    frame = frame.loc[frame["tf"].astype(str).eq(config.timeframe)].copy()
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True, errors="coerce")
    frame = frame.dropna(subset=["ts", config.future_return_column, *config.feature_columns])
    frame = frame.sort_values(["symbol", "ts"]).reset_index(drop=True)

    if frame.empty:
        report = {
            "status": "blocked",
            "reason": "empty_after_filter",
            "timeframe": config.timeframe,
        }
        write_json(metadata_path, report)
        return report

    frame[config.target_return_column] = frame[config.future_return_column].astype(float)
    frame[config.target_direction_column] = (frame[config.target_return_column] > 0).astype(int)
    frame["instrument"] = frame["symbol"].astype(str)
    frame["datetime"] = frame["ts"]

    selected_columns = [
        "instrument",
        "datetime",
        "symbol",
        "pair",
        "tf",
        "ts",
        "ts_ms",
        config.future_return_column,
        config.target_return_column,
        config.target_direction_column,
        *config.feature_columns,
    ]
    selected_columns = [col for col in selected_columns if col in frame.columns]
    dataset = frame[selected_columns].copy()

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    dataset.to_parquet(tmp, index=False)
    tmp.replace(target)

    feature_path = target.parent / "qlib_feature_columns.json"
    write_json(feature_path, {"feature_columns": config.feature_columns})

    metadata = {
        "status": "ok",
        "rows": int(len(dataset)),
        "symbols": sorted(dataset["symbol"].dropna().astype(str).unique().tolist()),
        "timeframe": config.timeframe,
        "target_column": config.target_direction_column,
        "target_return_column": config.target_return_column,
        "feature_count": len(config.feature_columns),
        "output_path": str(target),
        "feature_columns_path": str(feature_path),
        "qlib_runtime": qlib_runtime_status(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "note": "Dataset is exported in a Qlib-compatible instrument/datetime contract. Native qrun can consume this contract in a later phase.",
    }
    write_json(metadata_path, metadata)
    return metadata
