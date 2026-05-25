from __future__ import annotations

import time
from datetime import datetime, timezone

import pandas as pd

from smartcrypto.execution.signal_exporter import SignalExporter
from smartcrypto.risk.risk_manager import RiskManager
from smartcrypto.settings import RuntimeSettings


def run_once(settings: RuntimeSettings) -> None:
    settings.assert_safe()

    predictions = _load_predictions(settings.predictions_path)
    risk_manager = RiskManager.from_yaml("config/risk_limits.yml")
    exporter = SignalExporter(settings.signals_path)

    approved = [decision.signal for decision in risk_manager.approve_many(predictions) if decision.approved]
    exporter.write(
        runtime_mode=settings.runtime_mode,
        model_version=_model_version(predictions),
        signals=approved,
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
