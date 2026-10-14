from __future__ import annotations

import time
from datetime import datetime, timezone

import pandas as pd

from smartcrypto.execution.signal_exporter import SignalExporter
from smartcrypto.execution.signal_risk_gate import (
    DEFAULT_KILL_SWITCH_PATH,
    DEFAULT_RISK_LIMITS_PATH,
    apply_risk_manager_gate,
)
from smartcrypto.settings import RuntimeSettings


def run_once(settings: RuntimeSettings) -> None:
    settings.assert_safe()

    predictions = _load_predictions(settings.predictions_path)
    gate = apply_risk_manager_gate(
        predictions,
        risk_limits_path=DEFAULT_RISK_LIMITS_PATH,
        kill_switch_path=DEFAULT_KILL_SWITCH_PATH,
    )
    if gate.status != "ok":
        raise RuntimeError(f"signal_risk_gate_blocked:{gate.reason}")
    exporter = SignalExporter(settings.signals_path)
    exporter.write(
        runtime_mode=settings.runtime_mode,
        model_version=_model_version(predictions),
        signals=gate.approved_signals,
    )


def main() -> None:
    settings = RuntimeSettings.from_env()

    while True:
        started_at = datetime.now(timezone.utc).isoformat()
        try:
            run_once(settings)
            print({"status": "ok", "started_at": started_at}, flush=True)
        except Exception as exc:
            print({"status": "error", "started_at": started_at, "error": str(exc)}, flush=True)

        time.sleep(60)


def _load_predictions(path) -> list[dict]:
    if not path.exists():
        return []

    frame = pd.read_parquet(path)
    return frame.to_dict(orient="records")


def _model_version(predictions: list[dict]) -> str:
    for item in predictions:
        version = item.get("model_version")
        if version:
            return str(version)

    return "unknown"


if __name__ == "__main__":
    main()
