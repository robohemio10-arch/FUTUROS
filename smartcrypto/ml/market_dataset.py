from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml


@dataclass(frozen=True)
class MarketDatasetResult:
    status: str
    rows: int
    feature_count: int
    output_path: str
    target_column: str
    reason: str | None = None


def load_market_model_config(path: str | Path = "config/market_model.yml") -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def build_market_training_dataset(
    config_path: str | Path = "config/market_model.yml",
) -> MarketDatasetResult:
    config = load_market_model_config(config_path)
    model_config = config["market_model"]
    source_path = Path(model_config["source_path"])
    output_path = Path(model_config["training_output_path"])
    timeframe = str(model_config.get("timeframe", "5m"))
    horizon = int(model_config.get("target_horizon", 3))
    min_abs_return = float(model_config.get("min_abs_future_return", 0.0))
    target_return_column = f"future_ret_{horizon}"
    target_direction_column = f"target_direction_{horizon}"

    if not source_path.exists():
        raise FileNotFoundError(source_path)

    frame = pd.read_parquet(source_path)
    if frame.empty:
        raise ValueError(f"{source_path} está vazio")

    frame = frame.loc[frame["tf"].astype(str).eq(timeframe)].copy()
    if frame.empty:
        return MarketDatasetResult(
            status="blocked",
            reason=f"no_rows_for_timeframe_{timeframe}",
            rows=0,
            feature_count=0,
            output_path=str(output_path),
            target_column=target_direction_column,
        )

    if target_return_column not in frame.columns:
        raise ValueError(f"Coluna alvo ausente: {target_return_column}")

    frame["ts"] = pd.to_datetime(frame["ts"], utc=True, errors="coerce")
    frame = frame.dropna(subset=["ts", target_return_column]).copy()
    frame = frame.loc[frame[target_return_column].abs() >= min_abs_return].copy()

    frame[target_direction_column] = (frame[target_return_column] > 0).astype(int)
    frame[f"target_return_{horizon}"] = frame[target_return_column].astype(float)

    numeric_features = [name for name in config.get("features", {}).get("numeric", []) if name in frame.columns]
    categorical_features = [name for name in config.get("features", {}).get("categorical", []) if name in frame.columns]

    keep_columns = [
        "symbol",
        "pair",
        "tf",
        "ts",
        "ts_ms",
        target_return_column,
        f"target_return_{horizon}",
        target_direction_column,
        *numeric_features,
        *categorical_features,
    ]

    dataset = frame[keep_columns].replace([np.inf, -np.inf], np.nan)
    dataset = dataset.dropna(subset=[target_direction_column, *numeric_features]).copy()
    dataset = dataset.sort_values(["ts", "symbol"]).reset_index(drop=True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    dataset.to_parquet(output_path, index=False)

    return MarketDatasetResult(
        status="ok",
        rows=int(len(dataset)),
        feature_count=int(len(numeric_features) + len(categorical_features)),
        output_path=str(output_path),
        target_column=target_direction_column,
    )


def load_training_dataset(config_path: str | Path = "config/market_model.yml") -> tuple[pd.DataFrame, dict[str, Any]]:
    config = load_market_model_config(config_path)
    path = Path(config["market_model"]["training_output_path"])
    if not path.exists():
        build_market_training_dataset(config_path)
    frame = pd.read_parquet(path)
    return frame, config


def prepare_feature_matrix(
    frame: pd.DataFrame,
    config: dict[str, Any],
    reference_columns: list[str] | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    numeric_features = [name for name in config.get("features", {}).get("numeric", []) if name in frame.columns]
    categorical_features = [name for name in config.get("features", {}).get("categorical", []) if name in frame.columns]

    numeric = frame[numeric_features].apply(pd.to_numeric, errors="coerce").copy()
    categorical = pd.get_dummies(frame[categorical_features].astype(str), dummy_na=True) if categorical_features else pd.DataFrame(index=frame.index)

    matrix = pd.concat([numeric, categorical], axis=1)
    matrix = matrix.replace([np.inf, -np.inf], np.nan)
    matrix = matrix.fillna(matrix.median(numeric_only=True)).fillna(0.0)

    if reference_columns is not None:
        matrix = matrix.reindex(columns=reference_columns, fill_value=0.0)
        return matrix, reference_columns

    return matrix, list(matrix.columns)
