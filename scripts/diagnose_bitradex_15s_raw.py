from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(".")
OUT_DIR = ROOT / "data" / "reports" / "bitradex_15s_raw_diagnostics"

REALTIME_OUTPUT = ROOT / "bitradex_realtime_candle_collector_v1" / "data" / "output"
SQLITE_PATH = REALTIME_OUTPUT / "bitradex_live_candles.sqlite"

FILES = {
    "BTCUSDT": [
        REALTIME_OUTPUT / "bitradex_btcusdt_futures_15s.parquet",
        REALTIME_OUTPUT / "bitradex_btcusdt_futures_15s.csv",
    ],
    "ETHUSDT": [
        REALTIME_OUTPUT / "bitradex_ethusdt_futures_15s.parquet",
        REALTIME_OUTPUT / "bitradex_ethusdt_futures_15s.csv",
    ],
}

PRICE_GUARDS = {
    "BTCUSDT": {"min": 10000.0, "max": 300000.0},
    "ETHUSDT": {"min": 500.0, "max": 20000.0},
}


def read_any(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)

    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)

    raise RuntimeError(f"Formato não suportado: {path}")


def numeric_summary(df: pd.DataFrame, cols: list[str]) -> dict:
    out = {}

    for col in cols:
        if col not in df.columns:
            continue

        s = pd.to_numeric(df[col], errors="coerce")

        out[col] = {
            "non_null": int(s.notna().sum()),
            "nan": int(s.isna().sum()),
            "min": float(s.min()) if s.notna().any() else None,
            "p01": float(s.quantile(0.01)) if s.notna().any() else None,
            "p05": float(s.quantile(0.05)) if s.notna().any() else None,
            "median": float(s.median()) if s.notna().any() else None,
            "p95": float(s.quantile(0.95)) if s.notna().any() else None,
            "p99": float(s.quantile(0.99)) if s.notna().any() else None,
            "max": float(s.max()) if s.notna().any() else None,
        }

    return out


def find_price_columns(df: pd.DataFrame) -> list[str]:
    wanted = {"open", "high", "low", "close", "o", "h", "l", "c", "price", "last", "volume"}
    cols = []

    for col in df.columns:
        name = str(col).lower().strip()

        if name in wanted:
            cols.append(col)
        elif any(token in name for token in ["open", "high", "low", "close", "price", "last"]):
            cols.append(col)

    return cols


def normalize_symbol(value: object) -> str:
    return str(value).upper().replace("/", "").replace("_", "").replace("-", "").strip()


def file_report(symbol: str, path: Path) -> tuple[dict, pd.DataFrame]:
    if not path.exists():
        return {
            "symbol": symbol,
            "path": str(path),
            "exists": False,
        }, pd.DataFrame()

    df = read_any(path)

    price_cols = find_price_columns(df)
    guard = PRICE_GUARDS[symbol]

    work = df.copy()

    for col in price_cols:
        work[col] = pd.to_numeric(work[col], errors="coerce")

    valid_mask = pd.Series(True, index=work.index)

    for col in ["open", "high", "low", "close"]:
        if col in work.columns:
            valid_mask &= work[col].between(guard["min"], guard["max"])

    invalid = work[~valid_mask].copy()
    valid = work[valid_mask].copy()

    bad_sample = invalid.head(200).copy()

    report = {
        "symbol": symbol,
        "path": str(path),
        "exists": True,
        "rows": int(len(df)),
        "columns": [str(c) for c in df.columns],
        "price_columns_detected": [str(c) for c in price_cols],
        "valid_ohlc_rows_by_guard": int(len(valid)),
        "invalid_ohlc_rows_by_guard": int(len(invalid)),
        "numeric_summary": numeric_summary(work, price_cols),
        "head": df.head(5).astype(str).to_dict(orient="records"),
        "tail": df.tail(5).astype(str).to_dict(orient="records"),
    }

    return report, bad_sample


def sqlite_report() -> dict:
    if not SQLITE_PATH.exists():
        return {
            "exists": False,
            "path": str(SQLITE_PATH),
        }

    with sqlite3.connect(SQLITE_PATH) as conn:
        tables = pd.read_sql_query(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name",
            conn,
        )["name"].tolist()

        table_reports = {}

        for table in tables:
            try:
                sample = pd.read_sql_query(f'SELECT * FROM "{table}" LIMIT 5', conn)
                count = pd.read_sql_query(f'SELECT COUNT(*) AS n FROM "{table}"', conn)["n"].iloc[0]
                columns = sample.columns.tolist()

                table_reports[table] = {
                    "rows": int(count),
                    "columns": [str(c) for c in columns],
                    "sample": sample.astype(str).to_dict(orient="records"),
                }
            except Exception as exc:
                table_reports[table] = {
                    "error": str(exc),
                }

    return {
        "exists": True,
        "path": str(SQLITE_PATH),
        "tables": table_reports,
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    final = {
        "status": "ok",
        "mode": "bitradex_15s_raw_diagnostics",
        "sqlite": sqlite_report(),
        "files": {},
        "outputs": {},
    }

    for symbol, files in FILES.items():
        final["files"][symbol] = {}

        for path in files:
            report, bad_sample = file_report(symbol, path)
            final["files"][symbol][path.name] = report

            if len(bad_sample):
                out_bad = OUT_DIR / f"{symbol}_{path.stem}_invalid_price_sample.csv"
                bad_sample.to_csv(out_bad, index=False, encoding="utf-8-sig")
                final["outputs"][f"{symbol}_{path.stem}_invalid_sample"] = str(out_bad)

    out_json = OUT_DIR / "summary.json"
    out_json.write_text(json.dumps(final, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    print(json.dumps(final, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
