from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone


def status(path: str) -> dict:
    p = Path(path)
    return {"path": path, "exists": p.exists()}


def main() -> None:
    paths = {
        "compose": status("docker-compose.paper.yml"),
        "config": status("config/paper_cycle_reset.yml"),
        "reset_module": status("smartcrypto/execution/paper_cycle_reset.py"),
        "phase13_script": status("scripts/phase13_generate_active_signals.py"),
        "phase14_script": status("scripts/collect_phase14_closed_feedback.py"),
        "phase5_script": status("scripts/import_trades_incremental.py"),
        "training_dataset": status("data/features/training_dataset.parquet"),
        "trades_master": status("data/trades/trades_master.parquet"),
        "primary_signals": status("data/freqtrade_signals.json"),
        "pinned_signals": status("data/runtime/active_freqtrade_signals.json"),
        "exit_control": status("data/runtime/paper_exit_control.json"),
    }
    missing = [name for name, item in paths.items() if name not in {"compose", "phase5_script", "phase14_script"} and not item["exists"]]
    report = {
        "status": "ok" if not missing else "blocked",
        "paths": paths,
        "missing_paths": missing,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    Path("data/reports").mkdir(parents=True, exist_ok=True)
    Path("data/reports/phase17_preflight_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
