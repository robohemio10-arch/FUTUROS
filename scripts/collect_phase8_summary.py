from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from smartcrypto.qlib_engine.common import write_json


def load_json(path: str) -> dict:
    file = Path(path)
    if not file.exists():
        return {"exists": False}
    return {"exists": True, **json.loads(file.read_text(encoding="utf-8"))}


def main() -> None:
    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "phase": "phase8_qlib_prediction_engine",
        "preflight": load_json("data/reports/phase8_preflight_report.json"),
        "dataset": load_json("data/reports/phase8_qlib_dataset_report.json"),
        "training": load_json("data/reports/phase8_qlib_training_report.json"),
        "prediction": load_json("data/reports/phase8_qlib_prediction_report.json"),
        "signals": load_json("data/reports/phase8_qlib_signal_export_report.json"),
        "outputs": load_json("data/reports/phase8_output_summary.json"),
    }
    write_json("data/reports/phase8_summary.json", summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
