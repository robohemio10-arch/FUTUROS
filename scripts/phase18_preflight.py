from __future__ import annotations

import json
import os
from pathlib import Path

import yaml


def main() -> None:
    config_path = Path("config/paper_session.yml")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    required = [
        "docker-compose.paper.yml",
        "config/paper_session.yml",
        "config/risk_manager.yml",
        "smartcrypto/ops/paper_session.py",
        "smartcrypto/risk/risk_manager.py",
        "smartcrypto/execution/signal_producer.py",
    ]
    paths = {item: {"exists": Path(item).exists()} for item in required}
    live_flags = {name: os.getenv(name) for name in ["LIVE_ENABLED", "ORDER_SUBMISSION_ENABLED", "REAL_ORDER_SUBMISSION_ENABLED"]}
    blocked_flags = [name for name, value in live_flags.items() if str(value).lower() in {"1", "true", "yes", "on"}]
    report = {"status": "ok" if not blocked_flags and all(item["exists"] for item in paths.values()) else "blocked", "paths": paths, "live_flags": live_flags, "blocked_flags": blocked_flags, "config": config}
    Path("data/reports/paper_sessions").mkdir(parents=True, exist_ok=True)
    Path("data/reports/paper_sessions/phase18_preflight_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "ok":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
