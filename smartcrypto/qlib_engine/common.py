from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from smartcrypto.runtime.integrity_traceability_v2 import atomic_write_json


DEFAULT_CONFIG_PATH = Path("config/qlib_model.yml")


@dataclass(frozen=True)
class QlibEngineConfig:
    timeframe: str
    target_horizon: int
    min_rows_for_training: int
    test_size: float
    random_state: int
    model_version: str
    prediction_threshold: float
    max_position_usdt: float
    leverage: float
    signal_ttl_minutes: int
    feature_columns: list[str]

    @property
    def target_return_column(self) -> str:
        return f"target_return_{self.target_horizon}"

    @property
    def target_direction_column(self) -> str:
        return f"target_direction_{self.target_horizon}"

    @property
    def future_return_column(self) -> str:
        return f"future_ret_{self.target_horizon}"


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> QlibEngineConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return QlibEngineConfig(
        timeframe=str(raw["timeframe"]),
        target_horizon=int(raw["target_horizon"]),
        min_rows_for_training=int(raw["min_rows_for_training"]),
        test_size=float(raw["test_size"]),
        random_state=int(raw["random_state"]),
        model_version=str(raw["model_version"]),
        prediction_threshold=float(raw["prediction_threshold"]),
        max_position_usdt=float(raw["max_position_usdt"]),
        leverage=float(raw["leverage"]),
        signal_ttl_minutes=int(raw["signal_ttl_minutes"]),
        feature_columns=[str(item) for item in raw["feature_columns"]],
    )


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    atomic_write_json(path, payload, sort_keys=False)


def qlib_runtime_status() -> dict[str, Any]:
    try:
        import qlib  # type: ignore

        version = getattr(qlib, "__version__", "unknown")
        return {"available": True, "version": version}
    except Exception as exc:
        return {"available": False, "version": None, "error": str(exc)}
