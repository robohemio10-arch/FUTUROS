from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from smartcrypto.ml.model_guardrails import load_phase4_config, load_training_dataset, require_report_dir
from smartcrypto.ml.model_registry import write_json
from smartcrypto.ml.walk_forward import run_walk_forward_validation


def main() -> None:
    config = load_phase4_config()
    reports_dir = require_report_dir(config["reports_dir"])
    dataset_path = Path(config["training_dataset_path"])
    report_path = reports_dir / "phase4_walk_forward_report.json"

    if not dataset_path.exists():
        payload = {
            "status": "blocked",
            "reason": "training_dataset_missing",
            "dataset_path": str(dataset_path),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        write_json(report_path, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    frame = load_training_dataset(dataset_path)
    result = run_walk_forward_validation(
        frame=frame,
        target_column=config["target_column"],
        min_rows=int(config["min_trades_for_walk_forward"]),
        random_state=int(config["random_state"]),
    )

    payload = {
        "status": result.status,
        "reason": result.reason,
        "walk_forward": result.to_dict(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    write_json(report_path, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
