from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def module_exists(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def main() -> None:
    paths = {
        "signal_runtime_config": Path("config/signal_runtime.yml"),
        "primary_signals": Path("data/freqtrade_signals.json"),
        "pinned_signals": Path("data/runtime/active_freqtrade_signals.json"),
        "decision_log": Path("data/runtime/freqtrade_signal_decisions.jsonl"),
        "strategy": Path("freqtrade/user_data/strategies/SmartCryptoSignalStrategy.py"),
        "signal_store": Path("smartcrypto/execution/signal_store.py"),
        "db_reader": Path("smartcrypto/data/freqtrade_db_reader.py"),
    }
    report = {
        "status": "ok",
        "missing_modules": [m for m in ["pandas", "pyarrow", "yaml"] if not module_exists(m)],
        "paths": {key: {"path": str(path), "exists": path.exists()} for key, path in paths.items()},
    }
    report["missing_paths"] = [key for key, value in report["paths"].items() if not value["exists"] and key not in {"pinned_signals", "decision_log"}]
    if report["missing_modules"] or report["missing_paths"]:
        report["status"] = "blocked"
    Path("data/reports").mkdir(parents=True, exist_ok=True)
    Path("data/reports/phase12_preflight_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
