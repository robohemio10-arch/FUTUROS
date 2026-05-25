from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import yaml


def exists(path: str) -> dict[str, object]:
    p = Path(path)
    return {"path": path, "exists": p.exists()}


def module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def main() -> None:
    reports_dir = Path("data/reports")
    reports_dir.mkdir(parents=True, exist_ok=True)

    config_path = Path("config/ops_loop.yml")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}

    required_paths = {
        "compose": exists("docker-compose.paper.yml"),
        "ops_config": exists("config/ops_loop.yml"),
        "signals": exists("data/freqtrade_signals.json"),
        "qlib_predictions": exists("data/predictions/latest_qlib_predictions.parquet"),
        "qlib_model": exists("data/models/qlib_market_model.joblib"),
        "phase8_export_predictions": exists("scripts/export_qlib_predictions.py"),
        "phase8_export_signals": exists("scripts/export_qlib_freqtrade_signals.py"),
        "phase9_validator": exists("scripts/validate_freqtrade_signal_contract.py"),
        "phase9_execution_status": exists("scripts/collect_phase9_execution_status.py"),
        "phase7_collector": exists("scripts/collect_freqtrade_paper_history.py"),
        "phase5_importer": exists("scripts/import_trades_incremental.py"),
        "phase5_rebuild": exists("scripts/rebuild_phase5_datasets.py"),
    }

    missing_paths = [name for name, value in required_paths.items() if not value["exists"]]
    missing_modules = [name for name in ["pandas", "pyarrow", "yaml"] if not module_available(name)]

    report = {
        "status": "ok" if not missing_modules else "blocked",
        "missing_modules": missing_modules,
        "missing_paths": missing_paths,
        "paths": required_paths,
        "config": config,
    }

    output = reports_dir / "phase10_preflight_report.json"
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))

    if missing_modules:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
