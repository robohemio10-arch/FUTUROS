from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import importlib.util
import json


REQUIRED_PATHS = {
    "compose": Path("docker-compose.paper.yml"),
    "ops_config": Path("config/ops_loop.yml"),
    "signals": Path("data/freqtrade_signals.json"),
    "qlib_predictions": Path("data/predictions/latest_qlib_predictions.parquet"),
    "qlib_model": Path("data/models/qlib_market_model.joblib"),
    "decision_log": Path("data/runtime/freqtrade_signal_decisions.jsonl"),
    "phase11_signal_guard": Path("smartcrypto/execution/signal_contract_guard.py"),
    "phase11_db_reader": Path("smartcrypto/data/freqtrade_db_reader.py"),
}


def module_exists(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def main() -> None:
    report = {
        "status": "ok",
        "missing_modules": [name for name in ["pandas", "pyarrow", "yaml", "smartcrypto"] if not module_exists(name)],
        "paths": {
            name: {"path": str(path), "exists": path.exists()}
            for name, path in REQUIRED_PATHS.items()
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    report["missing_paths"] = [name for name, item in report["paths"].items() if not item["exists"] and name not in {"decision_log"}]
    if report["missing_modules"] or report["missing_paths"]:
        report["status"] = "blocked"

    Path("data/reports").mkdir(parents=True, exist_ok=True)
    Path("data/reports/phase11_preflight_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
