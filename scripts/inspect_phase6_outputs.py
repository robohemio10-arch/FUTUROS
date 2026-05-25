from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def describe_parquet(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"exists": False, "rows": None, "columns": None}
    frame = pd.read_parquet(p)
    return {"exists": True, "rows": int(len(frame)), "columns": list(frame.columns)}


def describe_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"exists": False}
    try:
        return {"exists": True, **json.loads(p.read_text(encoding="utf-8"))}
    except Exception as exc:
        return {"exists": True, "error": str(exc)}


def main() -> None:
    summary = {
        "market_features": describe_parquet("data/features/market_features_60d.parquet"),
        "market_training_dataset": describe_parquet("data/features/market_training_dataset.parquet"),
        "latest_predictions": describe_parquet("data/predictions/latest_market_predictions.parquet"),
        "freqtrade_signals": describe_json("data/freqtrade_signals.json"),
        "preflight_report": describe_json("data/reports/phase6_preflight_report.json"),
        "training_report": describe_json("data/reports/phase6_market_training_report.json"),
        "prediction_report": describe_json("data/reports/phase6_market_prediction_report.json"),
        "signal_report": describe_json("data/reports/phase6_signal_export_report.json"),
        "model_file": {"exists": Path("data/models/market_direction_model.joblib").exists()},
    }
    status = "ok"
    if summary["training_report"].get("status") == "blocked":
        status = "blocked"
    summary["phase6_status"] = {
        "status": status,
        "model_exists": summary["model_file"]["exists"],
        "predictions_exist": summary["latest_predictions"]["exists"],
    }
    Path("data/reports").mkdir(parents=True, exist_ok=True)
    Path("data/reports/phase6_output_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("VALIDATION_OK")


if __name__ == "__main__":
    main()
