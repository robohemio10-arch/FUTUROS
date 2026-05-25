from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


REPORTS = [
    "phase5_preflight_report.json",
    "phase5_import_report.json",
    "phase5_rebuild_report.json",
    "phase5_output_summary.json",
]


def load_json(path: Path) -> dict:
    if not path.exists():
        return {"exists": False}
    try:
        return {"exists": True, **json.loads(path.read_text(encoding="utf-8"))}
    except Exception as exc:
        return {"exists": True, "error": str(exc)}


def main() -> None:
    reports_dir = Path("data/reports")
    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reports": {
            name: load_json(reports_dir / name)
            for name in REPORTS
        },
        "files": {
            "trades_master_xlsx": Path("data/trades/trades_master.xlsx").exists(),
            "trades_master_parquet": Path("data/trades/trades_master.parquet").exists(),
            "trades_excel": Path("data/trades/trades_excel.xlsx").exists(),
            "trade_enriched": Path("data/features/trade_enriched.parquet").exists(),
            "training_dataset": Path("data/features/training_dataset.parquet").exists(),
            "sqlite": Path("data/sqlite/trading_dataset.sqlite").exists(),
        },
    }
    reports_dir.mkdir(parents=True, exist_ok=True)
    output = reports_dir / "phase5_summary.json"
    output.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
