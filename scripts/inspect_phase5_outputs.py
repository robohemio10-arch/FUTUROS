from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd


def inspect_table(path: Path) -> dict:
    if not path.exists():
        return {"exists": False, "rows": None, "columns": None}

    if path.suffix.lower() == ".parquet":
        frame = pd.read_parquet(path)
    elif path.suffix.lower() in {".xlsx", ".xls"}:
        frame = pd.read_excel(path)
    elif path.suffix.lower() == ".csv":
        frame = pd.read_csv(path)
    else:
        return {"exists": True, "rows": None, "columns": None, "unsupported": True}

    return {
        "exists": True,
        "rows": int(len(frame)),
        "columns": list(frame.columns),
    }


def inspect_json(path: Path) -> dict:
    if not path.exists():
        return {"exists": False}
    try:
        return {"exists": True, **json.loads(path.read_text(encoding="utf-8"))}
    except Exception as exc:
        return {"exists": True, "error": str(exc)}


def inspect_sqlite(path: Path) -> dict:
    if not path.exists():
        return {"exists": False, "tables": {}}

    tables = {}
    with sqlite3.connect(path) as connection:
        names = [
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type='table' order by name"
            ).fetchall()
        ]
        for name in names:
            tables[name] = int(connection.execute(f'select count(*) from "{name}"').fetchone()[0])

    return {"exists": True, "tables": tables}


def main() -> None:
    summary = {
        "trades_master_xlsx": inspect_table(Path("data/trades/trades_master.xlsx")),
        "trades_master_parquet": inspect_table(Path("data/trades/trades_master.parquet")),
        "trades_excel_compatibility": inspect_table(Path("data/trades/trades_excel.xlsx")),
        "trade_enriched": inspect_table(Path("data/features/trade_enriched.parquet")),
        "training_dataset": inspect_table(Path("data/features/training_dataset.parquet")),
        "sqlite": inspect_sqlite(Path("data/sqlite/trading_dataset.sqlite")),
        "reports": {
            "preflight": inspect_json(Path("data/reports/phase5_preflight_report.json")),
            "import": inspect_json(Path("data/reports/phase5_import_report.json")),
            "rebuild": inspect_json(Path("data/reports/phase5_rebuild_report.json")),
        },
        "phase5_status": {
            "status": "ok",
            "has_master": Path("data/trades/trades_master.parquet").exists() or Path("data/trades/trades_master.xlsx").exists(),
            "has_training_dataset": Path("data/features/training_dataset.parquet").exists(),
        },
    }

    Path("data/reports").mkdir(parents=True, exist_ok=True)
    Path("data/reports/phase5_output_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("VALIDATION_OK")


if __name__ == "__main__":
    main()
