from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


REPORT_PATH = Path("data/reports/phase5_rebuild_report.json")


def run_script(path: str) -> dict:
    completed = subprocess.run(
        [sys.executable, path],
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "script": path,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "ok": completed.returncode == 0,
    }


def main() -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    compatibility_xlsx = Path("data/trades/trades_excel.xlsx")
    master_xlsx = Path("data/trades/trades_master.xlsx")
    master_parquet = Path("data/trades/trades_master.parquet")

    if not compatibility_xlsx.exists():
        report = {
            "status": "blocked",
            "reason": "trades_excel_not_found",
            "compatibility_xlsx": str(compatibility_xlsx),
            "master_xlsx_exists": master_xlsx.exists(),
            "master_parquet_exists": master_parquet.exists(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return

    scripts = [
        "scripts/build_trade_enriched.py",
        "scripts/build_training_dataset.py",
    ]

    missing_scripts = [script for script in scripts if not Path(script).exists()]
    if missing_scripts:
        report = {
            "status": "error",
            "reason": "missing_rebuild_scripts",
            "missing_scripts": missing_scripts,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(report, indent=2, ensure_ascii=False))
        raise SystemExit(1)

    results = [run_script(script) for script in scripts]
    status = "ok" if all(result["ok"] for result in results) else "error"

    report = {
        "status": status,
        "steps": results,
        "outputs": {
            "trade_enriched": "data/features/trade_enriched.parquet",
            "training_dataset": "data/features/training_dataset.parquet",
            "sqlite": "data/sqlite/trading_dataset.sqlite",
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))

    if status != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
