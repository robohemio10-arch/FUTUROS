from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd


def safe_parquet(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"exists": False}
    frame = pd.read_parquet(p)
    return {
        "exists": True,
        "rows": int(len(frame)),
        "columns": list(frame.columns),
    }


def safe_excel(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"exists": False}
    frame = pd.read_excel(p)
    return {
        "exists": True,
        "rows": int(len(frame)),
        "columns": list(map(str, frame.columns)),
    }


def safe_sqlite(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"exists": False}
    con = sqlite3.connect(p)
    try:
        tables = {}
        for (name,) in con.execute("select name from sqlite_master where type='table' order by name"):
            tables[name] = int(con.execute(f'select count(*) from "{name}"').fetchone()[0])
        return {"exists": True, "tables": tables}
    finally:
        con.close()


def main() -> None:
    summary = {
        "status": "ok",
        "trades_excel": safe_excel("data/trades/trades_excel.xlsx"),
        "market_features": safe_parquet("data/features/market_features_60d.parquet"),
        "trade_enriched": safe_parquet("data/features/trade_enriched.parquet"),
        "training_dataset": safe_parquet("data/features/training_dataset.parquet"),
        "sqlite": safe_sqlite("data/sqlite/trading_dataset.sqlite"),
    }

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
