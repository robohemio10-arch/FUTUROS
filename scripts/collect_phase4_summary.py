from __future__ import annotations

import json
from pathlib import Path

from smartcrypto.ml.model_registry import write_json


def read_json(path: Path) -> dict:
    if not path.exists():
        return {"exists": False}

    try:
        return {"exists": True, "payload": json.loads(path.read_text(encoding="utf-8"))}
    except Exception as exc:
        return {"exists": True, "error": str(exc)}


def main() -> None:
    reports_dir = Path("data/reports")
    payload = {
        "phase": "phase4_baseline_ml",
        "reports": {
            "preflight": read_json(reports_dir / "phase4_preflight_report.json"),
            "training": read_json(reports_dir / "phase4_baseline_training_report.json"),
            "walk_forward": read_json(reports_dir / "phase4_walk_forward_report.json"),
            "registry": read_json(reports_dir / "phase4_model_registry.json"),
            "outputs": read_json(reports_dir / "phase4_output_summary.json"),
        },
    }

    output_path = reports_dir / "phase4_summary.json"
    write_json(output_path, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
