from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimeSettings:
    runtime_mode: str
    live_enabled: bool
    order_submission_enabled: bool
    real_order_submission_enabled: bool
    database_path: Path
    signals_path: Path
    predictions_path: Path

    @classmethod
    def from_env(cls) -> "RuntimeSettings":
        return cls(
            runtime_mode=os.getenv("SMARTCRYPTO_RUNTIME_MODE", "paper"),
            live_enabled=_as_bool(os.getenv("LIVE_ENABLED", "false")),
            order_submission_enabled=_as_bool(os.getenv("ORDER_SUBMISSION_ENABLED", "false")),
            real_order_submission_enabled=_as_bool(os.getenv("REAL_ORDER_SUBMISSION_ENABLED", "false")),
            database_path=Path(os.getenv("SMARTCRYPTO_DB_PATH", "data/sqlite/trading_dataset.sqlite")),
            signals_path=Path(os.getenv("SMARTCRYPTO_SIGNALS_PATH", "data/freqtrade_signals.json")),
            predictions_path=Path(
                os.getenv(
                    "SMARTCRYPTO_PREDICTIONS_PATH",
                    "data/predictions/latest_qlib_predictions.parquet",
                )
            ),
        )

    def assert_safe(self) -> None:
        if self.runtime_mode == "paper" and self.real_order_submission_enabled:
            raise RuntimeError("paper mode cannot submit real orders")

        if self.runtime_mode != "live" and self.live_enabled:
            raise RuntimeError("LIVE_ENABLED requires runtime_mode=live")


def load_json_config(path: str | Path) -> dict:
    config_path = Path(path)
    if not config_path.exists():
        return {}

    return json.loads(config_path.read_text(encoding="utf-8"))


def _as_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}
