from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from smartcrypto.ml.model_guardrails import load_phase4_config
from smartcrypto.ml.model_registry import write_json


def read_json(path: Path) -> dict:
    if not path.exists():
        return {"exists": False}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"exists": True, "error": str(exc)}

    return {"exists": True, **payload}


def parquet_summary(path: Path) -> dict:
    if not path.exists():
        return {"exists": False, "rows": None, "columns": None}

    frame = pd.read_parquet(path)
    return {"exists": True, "rows": int(len(frame)), "columns": list(frame.columns)}


def main() -> None:
    config = load_phase4_config()
    reports_dir = Path(config["reports_dir"])
    models_dir = Path(config["models_dir"])
    dataset_path = Path(config["training_dataset_path"])

    summary = {
        "training_dataset": parquet_summary(dataset_path),
        "preflight_report": read_json(reports_dir / "phase4_preflight_report.json"),
        "training_report": read_json(reports_dir / "phase4_baseline_training_report.json"),
        "walk_forward_report": read_json(reports_dir / "phase4_walk_forward_report.json"),
        "model_registry": read_json(reports_dir / "phase4_model_registry.json"),
        "model_file": {
            "exists": (models_dir / "baseline_random_forest.joblib").exists(),
            "path": str(models_dir / "baseline_random_forest.joblib"),
        },
    }

    status = "ok"

    if summary["training_dataset"]["exists"] is False:
        status = "blocked"

    training_status = summary["training_report"].get("status")
    walk_forward_status = summary["walk_forward_report"].get("status")

    summary["phase4_status"] = {
        "status": status,
        "training_status": training_status,
        "walk_forward_status": walk_forward_status,
        "production_blocked": training_status != "trained" or walk_forward_status != "validated",
    }

    output_path = reports_dir / "phase4_output_summary.json"
    write_json(output_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("VALIDATION_OK")


if __name__ == "__main__":
    main()
