from __future__ import annotations

import json
from pathlib import Path


def read_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def main() -> int:
    report_path = Path("data/reports/phase21_qlib_walkforward_report.json")
    output_dir = Path("data/reports/phase21_walkforward")
    report = read_json(report_path)

    payload = {
        "report": {
            "path": str(report_path),
            "exists": report_path.exists(),
            "status": report.get("status") if isinstance(report, dict) else None,
            "reason": report.get("reason") if isinstance(report, dict) else None,
            "model_backend": report.get("model_backend") if isinstance(report, dict) else None,
            "folds_completed": report.get("folds_completed") if isinstance(report, dict) else None,
            "metrics": report.get("metrics") if isinstance(report, dict) else None,
            "error": report.get("error") if isinstance(report, dict) else None,
        },
        "output_dir": {
            "path": str(output_dir),
            "exists": output_dir.exists(),
            "files": sorted([path.name for path in output_dir.glob("*")]) if output_dir.exists() else [],
        },
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    if isinstance(report, dict) and report.get("status") in {"ok", "skipped"}:
        print("VALIDATION_OK")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
