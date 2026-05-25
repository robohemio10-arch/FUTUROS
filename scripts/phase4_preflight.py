from __future__ import annotations

import importlib
import json
from pathlib import Path

from smartcrypto.ml.model_guardrails import (
    evaluate_training_guardrails,
    load_phase4_config,
    load_training_dataset,
    require_report_dir,
)
from smartcrypto.ml.model_registry import write_json


def main() -> None:
    config = load_phase4_config()
    required_modules = ["pandas", "numpy", "pyarrow", "sklearn", "joblib", "yaml"]
    missing_modules = []

    for module in required_modules:
        try:
            importlib.import_module(module)
        except Exception:
            missing_modules.append(module)

    dataset_path = Path(config["training_dataset_path"])
    reports_dir = require_report_dir(config["reports_dir"])
    report_path = reports_dir / "phase4_preflight_report.json"

    payload = {
        "status": "ok",
        "missing_modules": missing_modules,
        "training_dataset_path": str(dataset_path),
        "training_dataset_exists": dataset_path.exists(),
        "guardrail": None,
    }

    if dataset_path.exists():
        frame = load_training_dataset(dataset_path)
        decision = evaluate_training_guardrails(
            frame=frame,
            target_column=config["target_column"],
            min_trades_for_training=int(config["min_trades_for_training"]),
            min_trades_for_walk_forward=int(config["min_trades_for_walk_forward"]),
            min_classes=int(config["min_classes"]),
        )
        payload["guardrail"] = decision.to_dict()

    if missing_modules:
        payload["status"] = "error"

    write_json(report_path, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if missing_modules:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
