from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd


EXCLUDED_COLUMNS = {
    "order_id",
    "close_side",
    "freqtrade_pair",
    "quality_flag",
}


def build_training_dataset(
    trade_enriched_path: str | Path,
    output_path: str | Path,
    sqlite_path: str | Path | None = None,
    report_path: str | Path | None = None,
) -> pd.DataFrame:
    source = Path(trade_enriched_path)
    if not source.exists():
        raise FileNotFoundError(f"trade_enriched não encontrado: {source}")

    trades = pd.read_parquet(source)
    dataset = trades.copy()
    dataset["target_win"] = dataset["is_win"].astype(int)
    dataset["target_return_pct"] = dataset["return_pct"]
    dataset["target_positive_mfe"] = (dataset["mfe_pct"].fillna(0) > 0).astype(int)

    for column in dataset.columns:
        if dataset[column].dtype == "object" and column not in EXCLUDED_COLUMNS:
            dataset[column] = dataset[column].astype("category").cat.codes.replace(-1, np.nan)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    dataset.to_parquet(output, index=False)

    if sqlite_path:
        sqlite_target = Path(sqlite_path)
        sqlite_target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(sqlite_target) as con:
            dataset.to_sql("training_dataset", con, if_exists="replace", index=False)

    summary = {
        "status": "ok",
        "rows": int(len(dataset)),
        "columns": int(len(dataset.columns)),
        "targets": ["target_win", "target_return_pct", "target_positive_mfe"],
        "output": str(output),
        "sqlite": str(sqlite_path) if sqlite_path else None,
    }

    if report_path:
        target = Path(report_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    return dataset
