from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from smartcrypto.execution.paper_exit_control import find_existing_db, load_config


def module_exists(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def main() -> None:
    config = load_config()
    required = {
        "config": Path("config/paper_exit_control.yml"),
        "strategy": Path("freqtrade/user_data/strategies/SmartCryptoSignalStrategy.py"),
        "module": Path("smartcrypto/execution/paper_exit_control.py"),
        "script_generate": Path("scripts/generate_phase15_exit_signal.py"),
        "script_inspect": Path("scripts/inspect_phase15_exit_flow.py"),
    }
    paths = {name: {"path": str(path), "exists": path.exists()} for name, path in required.items()}
    db_candidates = [{"path": str(path), "exists": path.exists()} for path in config.db_candidates]
    missing_paths = [name for name, result in paths.items() if not result["exists"]]
    missing_modules = [name for name in ["yaml", "pandas"] if not module_exists(name)]
    db_path = find_existing_db(config.db_candidates)
    report = {
        "status": "ok" if not missing_paths and not missing_modules and db_path else "blocked",
        "paths": paths,
        "db_candidates": db_candidates,
        "db_path": str(db_path) if db_path else None,
        "missing_paths": missing_paths,
        "missing_modules": missing_modules,
    }
    Path("data/reports").mkdir(parents=True, exist_ok=True)
    Path("data/reports/phase15_preflight_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
