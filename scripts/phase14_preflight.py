from __future__ import annotations

import json
from pathlib import Path

from smartcrypto.data.paper_trade_lifecycle import load_config, utc_now, write_json


REQUIRED_PATHS = {
    "compose": Path("docker-compose.paper.yml"),
    "config": Path("config/paper_feedback.yml"),
    "module": Path("smartcrypto/data/paper_trade_lifecycle.py"),
    "open_positions_script": Path("scripts/inspect_phase14_open_positions.py"),
    "closed_feedback_script": Path("scripts/collect_phase14_closed_feedback.py"),
    "outputs_script": Path("scripts/inspect_phase14_outputs.py"),
    "summary_script": Path("scripts/collect_phase14_summary.py"),
}


def main() -> None:
    cfg = load_config()
    paths = {
        key: {"path": str(path), "exists": path.exists()}
        for key, path in REQUIRED_PATHS.items()
    }
    db_candidates = [
        {"path": str(path), "exists": path.exists()}
        for path in cfg.db_candidates
    ]
    missing_paths = [
        key for key, payload in paths.items()
        if key != "compose" and not payload["exists"]
    ]

    report = {
        "status": "ok" if not missing_paths else "blocked",
        "paths": paths,
        "db_candidates": db_candidates,
        "missing_paths": missing_paths,
        "config": {
            "max_open_trades": cfg.max_open_trades,
            "expected_pairs": list(cfg.expected_pairs),
            "raw_export": str(cfg.raw_export),
            "inbox_export_csv": str(cfg.inbox_export_csv),
        },
        "created_at": utc_now(),
    }
    output_path = Path("data/reports/phase14_preflight_report.json")
    write_json(output_path, report)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
