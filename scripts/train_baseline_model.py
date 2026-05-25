from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from smartcrypto.ml.baseline_model import train_baseline_classifier
from smartcrypto.ml.model_guardrails import (
    evaluate_training_guardrails,
    load_phase4_config,
    load_training_dataset,
    require_report_dir,
)
from smartcrypto.ml.model_registry import registry_payload, write_json


def main() -> None:
    config = load_phase4_config()
    reports_dir = require_report_dir(config["reports_dir"])
    models_dir = Path(config["models_dir"])
    models_dir.mkdir(parents=True, exist_ok=True)

    dataset_path = Path(config["training_dataset_path"])
    report_path = reports_dir / "phase4_baseline_training_report.json"
    registry_path = reports_dir / "phase4_model_registry.json"
    model_path = models_dir / "baseline_random_forest.joblib"

    if not dataset_path.exists():
        payload = {
            "status": "blocked",
            "reason": "training_dataset_missing",
            "dataset_path": str(dataset_path),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        write_json(report_path, payload)
        write_json(
            registry_path,
            registry_payload(
                status="blocked",
                model_name=config["model_name"],
                model_path=None,
                training_report_path=str(report_path),
                walk_forward_report_path=None,
                production_enabled=False,
                reason="training_dataset_missing",
            ),
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    frame = load_training_dataset(dataset_path)
    decision = evaluate_training_guardrails(
        frame=frame,
        target_column=config["target_column"],
        min_trades_for_training=int(config["min_trades_for_training"]),
        min_trades_for_walk_forward=int(config["min_trades_for_walk_forward"]),
        min_classes=int(config["min_classes"]),
    )

    if not decision.trainable:
        payload = {
            "status": "blocked",
            "reason": decision.reason,
            "guardrail": decision.to_dict(),
            "model_exported": False,
            "production_enabled": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        write_json(report_path, payload)
        write_json(
            registry_path,
            registry_payload(
                status="blocked",
                model_name=config["model_name"],
                model_path=None,
                training_report_path=str(report_path),
                walk_forward_report_path=None,
                production_enabled=False,
                reason=decision.reason,
            ),
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    result = train_baseline_classifier(
        frame=frame,
        target_column=config["target_column"],
        model_path=model_path,
        random_state=int(config["random_state"]),
        test_size_fraction=float(config["test_size_fraction"]),
    )

    payload = {
        "status": "trained",
        "reason": None,
        "guardrail": decision.to_dict(),
        "training": result.to_dict(),
        "model_exported": True,
        "production_enabled": bool(config.get("production_enabled", False)),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    write_json(report_path, payload)
    write_json(
        registry_path,
        registry_payload(
            status="trained",
            model_name=config["model_name"],
            model_path=str(model_path),
            training_report_path=str(report_path),
            walk_forward_report_path=None,
            production_enabled=bool(config.get("production_enabled", False)),
            reason=None,
        ),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
