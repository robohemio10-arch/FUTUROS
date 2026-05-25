from __future__ import annotations

import json
from pathlib import Path


def read_json(path: Path):
    if not path.exists():
        return {"exists": False}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {"exists": True, **payload}
    except Exception as exc:
        return {"exists": True, "error": str(exc)}


def main() -> None:
    summary = {
        "phase": "phase9_signal_execution_validation",
        "preflight": read_json(Path("data/reports/phase9_preflight_report.json")),
        "test_signal": read_json(Path("data/reports/phase9_test_signal_report.json")),
        "signal_contract": read_json(Path("data/reports/phase9_signal_contract_report.json")),
        "execution_status": read_json(Path("data/reports/phase9_execution_status_report.json")),
        "output_summary": read_json(Path("data/reports/phase9_output_summary.json")),
    }

    output = Path("data/reports/phase9_summary.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
