from __future__ import annotations
import json, sqlite3
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd


def read_json(path: Path) -> dict:
    if not path.exists():
        return {"exists": False}
    try:
        return {"exists": True, "content": json.loads(path.read_text(encoding="utf-8"))}
    except Exception as exc:
        return {"exists": True, "error": str(exc)}


def table_info(path: Path) -> dict:
    if not path.exists():
        return {"exists": False, "rows": None, "columns": None, "min_ts": None, "max_ts": None}
    try:
        frame = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
        ts = pd.to_datetime(frame["ts"], utc=True, errors="coerce") if "ts" in frame.columns else None
        return {
            "exists": True,
            "rows": int(len(frame)),
            "columns": list(frame.columns),
            "symbols": sorted(frame["symbol"].dropna().astype(str).unique().tolist()) if "symbol" in frame.columns else [],
            "timeframes": sorted(frame["tf"].dropna().astype(str).unique().tolist()) if "tf" in frame.columns else [],
            "min_ts": ts.min().isoformat() if ts is not None and not ts.dropna().empty else None,
            "max_ts": ts.max().isoformat() if ts is not None and not ts.dropna().empty else None,
        }
    except Exception as exc:
        return {"exists": True, "error": str(exc)}

def sqlite_tables(path: Path) -> dict:
    if not path.exists():
        return {"exists": False, "tables": {}}
    tables = {}
    with sqlite3.connect(path) as connection:
        names = pd.read_sql_query("select name from sqlite_master where type='table'", connection)["name"].tolist()
        for name in names:
            try:
                tables[name] = int(pd.read_sql_query(f'select count(*) as n from "{name}"', connection)["n"].iloc[0])
            except Exception as exc:
                tables[name] = str(exc)
    return {"exists": True, "tables": tables}

def trades_range(path: Path) -> dict:
    if not path.exists():
        return {"exists": False}
    try:
        frame = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_excel(path)
        open_ts = pd.to_datetime(frame.get("horario_abertura"), utc=True, errors="coerce")
        close_ts = pd.to_datetime(frame.get("horario_fechamento"), utc=True, errors="coerce")
        combined = pd.concat([open_ts, close_ts]).dropna()
        return {"exists": True, "rows": int(len(frame)), "min_trade_ts": combined.min().isoformat() if not combined.empty else None, "max_trade_ts": combined.max().isoformat() if not combined.empty else None}
    except Exception as exc:
        return {"exists": True, "error": str(exc)}

def main() -> None:
    report = {
        "status": "ok",
        "phase": "phase22_historical_market_backfill",
        "raw_files": [str(path) for path in Path("data/raw/binance_futures_klines").glob("*")],
        "backfill_features": table_info(Path("data/features/market_features_1m_backfill.parquet")),
        "main_features": table_info(Path("data/features/market_features_60d.parquet")),
        "trade_enriched": table_info(Path("data/features/trade_enriched.parquet")),
        "training_dataset": table_info(Path("data/features/training_dataset.parquet")),
        "trades_master": trades_range(Path("data/trades/trades_master.parquet")),
        "sqlite": sqlite_tables(Path("data/sqlite/trading_dataset.sqlite")),
        "reports": {
            "preflight": Path("data/reports/phase22_preflight_report.json").exists(),
            "download": Path("data/reports/phase22_download_report.json").exists(),
            "features": Path("data/reports/phase22_features_report.json").exists(),
            "data_quality": Path("data/reports/phase22_data_quality_report.json").exists(),
            "phase5_rebuild": Path("data/reports/phase5_rebuild_report.json").exists(),
        },
        "features_report": read_json(Path("data/reports/phase22_features_report.json")),
        "data_quality_report": read_json(Path("data/reports/phase22_data_quality_report.json")),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    Path("data/reports/phase22_output_summary.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if not report["backfill_features"].get("exists") or not report["main_features"].get("exists"):
        raise SystemExit(1)

if __name__ == "__main__":
    main()
