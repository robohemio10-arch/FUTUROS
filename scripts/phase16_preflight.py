from __future__ import annotations

import json
from pathlib import Path

import yaml

from smartcrypto.execution.paper_force_close import find_db_path, read_config


def main() -> None:
    config_path = Path("config/paper_force_close.yml")
    cfg = read_config(config_path)

    paths = {
        "config": config_path,
        "module": Path("smartcrypto/execution/paper_force_close.py"),
        "force_close_script": Path("scripts/force_close_phase16_paper_trades.py"),
        "inspect_script": Path("scripts/inspect_phase16_outputs.py"),
        "summary_script": Path("scripts/collect_phase16_summary.py"),
        "market_features": cfg.market_features_path,
    }

    db_candidates = [
        {"path": str(path), "exists": path.exists()} for path in cfg.db_candidates
    ]
    db_path = find_db_path(cfg.db_candidates)

    report = {
        "status": "ok" if db_path else "blocked",
        "reason": None if db_path else "freqtrade_db_not_found",
        "paths": {name: {"path": str(path), "exists": path.exists()} for name, path in paths.items()},
        "db_candidates": db_candidates,
        "db_path": str(db_path) if db_path else None,
        "missing_paths": [name for name, path in paths.items() if not path.exists()],
    }

    Path("data/reports").mkdir(parents=True, exist_ok=True)
    Path("data/reports/phase16_preflight_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
