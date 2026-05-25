from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.qlib_engine.common import write_json


def parquet_summary(path: str) -> dict[str, Any]:
    file = Path(path)
    if not file.exists():
        return {"exists": False, "rows": None, "columns": None}
    frame = pd.read_parquet(file)
    return {"exists": True, "rows": int(len(frame)), "columns": list(frame.columns)}


def json_summary(path: str) -> dict[str, Any]:
    file = Path(path)
    if not file.exists():
        return {"exists": False}
    return {"exists": True, **json.loads(file.read_text(encoding="utf-8"))}


def main() -> None:
    summary = {
        "qlib_dataset": parquet_summary("data/qlib/qlib_market_dataset.parquet"),
        "latest_qlib_predictions": parquet_summary("data/predictions/latest_qlib_predictions.parquet"),
        "freqtrade_signals": json_summary("data/freqtrade_signals.json"),
        "preflight_report": json_summary("data/reports/phase8_preflight_report.json"),
        "dataset_report": json_summary("data/reports/phase8_qlib_dataset_report.json"),
        "training_report": json_summary("data/reports/phase8_qlib_training_report.json"),
        "prediction_report": json_summary("data/reports/phase8_qlib_prediction_report.json"),
        "signal_report": json_summary("data/reports/phase8_qlib_signal_export_report.json"),
        "model_file": {"exists": Path("data/models/qlib_market_model.joblib").exists()},
    }
    summary["phase8_status"] = {
        "status": "ok",
        "model_exists": summary["model_file"]["exists"],
        "predictions_exist": summary["latest_qlib_predictions"]["exists"],
        "signals_exist": summary["freqtrade_signals"]["exists"],
    }
    write_json("data/reports/phase8_output_summary.json", summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    print("VALIDATION_OK")


if __name__ == "__main__":
    main()
