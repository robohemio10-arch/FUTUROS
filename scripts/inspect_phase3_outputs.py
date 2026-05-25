from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd


PATHS = {
    "market_features": Path("data/features/market_features_60d.parquet"),
    "trades_excel": Path("data/trades/trades_excel.xlsx"),
    "trade_enriched": Path("data/features/trade_enriched.parquet"),
    "training_dataset": Path("data/features/training_dataset.parquet"),
    "sqlite": Path("data/sqlite/trading_dataset.sqlite"),
}


def parquet_summary(path: Path) -> dict:
    if not path.exists():
        return {"exists": False, "rows": None, "columns": None}
    frame = pd.read_parquet(path)
    return {
        "exists": True,
        "rows": int(len(frame)),
        "columns": list(frame.columns),
    }


def excel_summary(path: Path) -> dict:
    if not path.exists():
        return {"exists": False, "rows": None, "columns": None}
    frame = pd.read_excel(path)
    return {
        "exists": True,
        "rows": int(len(frame)),
        "columns": list(map(str, frame.columns)),
    }


def sqlite_summary(path: Path) -> dict:
    if not path.exists():
        return {"exists": False, "tables": {}}

    con = sqlite3.connect(path)
    try:
        table_names = [
            row[0]
            for row in con.execute("select name from sqlite_master where type='table' order by name").fetchall()
        ]
        tables = {}
        for name in table_names:
            tables[name] = int(con.execute(f'select count(*) from "{name}"').fetchone()[0])
        return {"exists": True, "tables": tables}
    finally:
        con.close()


def main() -> None:
    summary = {
        "market_features": parquet_summary(PATHS["market_features"]),
        "trades_excel": excel_summary(PATHS["trades_excel"]),
        "trade_enriched": parquet_summary(PATHS["trade_enriched"]),
        "training_dataset": parquet_summary(PATHS["training_dataset"]),
        "sqlite": sqlite_summary(PATHS["sqlite"]),
    }

    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if not summary["market_features"]["exists"]:
        raise SystemExit("market_features ausente")
    if summary["trades_excel"]["exists"]:
        if not summary["trade_enriched"]["exists"]:
            raise SystemExit("trade_enriched ausente apesar de trades_excel existir")
        if not summary["training_dataset"]["exists"]:
            raise SystemExit("training_dataset ausente apesar de trades_excel existir")

    print("VALIDATION_OK")


if __name__ == "__main__":
    main()
