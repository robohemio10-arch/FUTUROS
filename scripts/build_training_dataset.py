from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine


ENRICHED_PATH = Path("data/features/trade_enriched.parquet")
OUT_PATH = Path("data/features/training_dataset.parquet")
REPORT_PATH = Path("data/reports/training_dataset_summary.json")
DB_PATH = Path("data/sqlite/trading_dataset.sqlite")


def main() -> None:
    if not ENRICHED_PATH.exists():
        raise FileNotFoundError(ENRICHED_PATH)

    frame = pd.read_parquet(ENRICHED_PATH)

    blocked_prefixes = (
        "horario_",
        "preco_",
        "volume_",
        "taxa_",
    )
    blocked_columns = {
        "moeda",
        "fechar_side",
        "order_id",
        "open_ts",
        "close_ts",
        "entry_price",
        "exit_price",
        "pnl",
        "target_win",
        "return_pct",
    }

    feature_columns = []
    for column in frame.columns:
        if column in blocked_columns:
            continue
        if column.startswith(blocked_prefixes):
            continue
        if column.startswith(("open_", "close_")) and column not in {"open_ts", "close_ts"}:
            feature_columns.append(column)
        if column in {"duration_seconds", "mfe_pct", "mae_pct", "path_candles"}:
            feature_columns.append(column)

    keep_columns = ["trade_id", "symbol", "target_win", "return_pct", "duration_seconds", *feature_columns]
    keep_columns = list(dict.fromkeys([column for column in keep_columns if column in frame.columns]))

    dataset = frame[keep_columns].copy()
    dataset = dataset.replace([float("inf"), float("-inf")], pd.NA)
    dataset.to_parquet(OUT_PATH, index=False)

    if DB_PATH.exists():
        engine = create_engine(f"sqlite:///{DB_PATH}")
        dataset.to_sql("training_dataset", engine, if_exists="replace", index=False)

    summary = {
        "status": "ok",
        "rows": int(len(dataset)),
        "columns": int(len(dataset.columns)),
        "feature_columns": [column for column in dataset.columns if column not in {"trade_id", "symbol", "target_win", "return_pct"}],
        "output": str(OUT_PATH),
        "sqlite_table": "training_dataset" if DB_PATH.exists() else None,
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
