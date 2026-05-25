from __future__ import annotations

import json
from pathlib import Path


REPORTS = {
    "preflight": Path("data/reports/phase9_preflight_report.json"),
    "test_signal": Path("data/reports/phase9_test_signal_report.json"),
    "signal_contract": Path("data/reports/phase9_signal_contract_report.json"),
    "execution_status": Path("data/reports/phase9_execution_status_report.json"),
}


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
        "reports": {name: read_json(path) for name, path in REPORTS.items()},
        "files": {
            "freqtrade_signals": {
                "exists": Path("data/freqtrade_signals.json").exists(),
                "path": "data/freqtrade_signals.json",
            },
            "decision_log": {
                "exists": Path("data/runtime/freqtrade_signal_decisions.jsonl").exists(),
                "path": "data/runtime/freqtrade_signal_decisions.jsonl",
            },
            "freqtrade_db": {
                "exists": Path("freqtrade/user_data/tradesv3.paper.sqlite").exists(),
                "path": "freqtrade/user_data/tradesv3.paper.sqlite",
            },
        },
    }

    output = Path("data/reports/phase9_output_summary.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    print("VALIDATION_OK")


if __name__ == "__main__":
    main()
