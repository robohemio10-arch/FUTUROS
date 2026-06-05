from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd


def build_sqlite(
    market_features_path: str | Path,
    trades_path: str | Path,
    output_db: str | Path,
) -> Path:
    db_path = Path(output_db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as connection:
        market_features = pd.read_parquet(market_features_path)
        _to_sql_ready(market_features).to_sql("market_features", connection, if_exists="replace", index=False)

        trade_source = Path(trades_path)
        trades_loaded = trade_source.exists()

        if trades_loaded:
            trades = _read_table(trade_source)
            _to_sql_ready(trades).to_sql("trades_excel", connection, if_exists="replace", index=False)

        connection.execute("CREATE INDEX IF NOT EXISTS idx_mf_symbol_ts_tf ON market_features(symbol, ts, tf)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_mf_pair_ts_tf ON market_features(pair, ts, tf)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_mf_ts ON market_features(ts)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_mf_symbol_tf ON market_features(symbol, tf)")

        if trades_loaded:
            connection.execute("CREATE INDEX IF NOT EXISTS idx_tx_moeda ON trades_excel(moeda)")

    return db_path


def _read_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()

    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path)

    if suffix == ".csv":
        return pd.read_csv(path)

    if suffix == ".parquet":
        return pd.read_parquet(path)

    raise ValueError(f"unsupported source: {path}")


def _to_sql_ready(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in result.columns:
        if pd.api.types.is_datetime64_any_dtype(result[column]):
            result[column] = pd.to_datetime(result[column], utc=True, errors="coerce").dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return result
