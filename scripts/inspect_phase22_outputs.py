from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.market.market_feature_schema import lookahead_columns  # noqa: E402
from smartcrypto.data.trader_master_fingerprint_v2.legacy_master_governance import (  # noqa: E402
    DEFAULT_MASTER,
)
from smartcrypto.data.trader_master_fingerprint_v2.master_adapter import (  # noqa: E402
    MasterReadBundle,
    read_trader_master_readonly,
)


def read_json(path: Path) -> dict:
    if not path.exists():
        return {"exists": False}
    try:
        return {"exists": True, "content": json.loads(path.read_text(encoding="utf-8"))}
    except Exception as exc:
        return {"exists": True, "error": str(exc)}


def table_info(path: Path, *, operational: bool = False) -> dict:
    if not path.exists():
        return {
            "exists": False,
            "rows": None,
            "columns": None,
            "min_ts": None,
            "max_ts": None,
            "lookahead_columns": [],
            "lookahead_columns_count": 0,
            "operational_feature_schema_ok": not operational,
            "status": "missing",
        }
    try:
        frame = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
        ts = pd.to_datetime(frame["ts"], utc=True, errors="coerce") if "ts" in frame.columns else None
        found_lookahead = lookahead_columns(frame)
        return {
            "exists": True,
            "rows": int(len(frame)),
            "columns": list(frame.columns),
            "lookahead_columns": found_lookahead,
            "lookahead_columns_count": len(found_lookahead),
            "operational_feature_schema_ok": not found_lookahead if operational else True,
            "status": "warning" if operational and found_lookahead else "ok",
            "symbols": sorted(frame["symbol"].dropna().astype(str).unique().tolist()) if "symbol" in frame.columns else [],
            "timeframes": sorted(frame["tf"].dropna().astype(str).unique().tolist()) if "tf" in frame.columns else [],
            "min_ts": ts.min().isoformat() if ts is not None and not ts.dropna().empty else None,
            "max_ts": ts.max().isoformat() if ts is not None and not ts.dropna().empty else None,
        }
    except Exception as exc:
        return {"exists": True, "error": str(exc), "status": "error"}

def sqlite_tables(path: Path) -> dict:
    if not path.exists():
        return {"exists": False, "tables": {}}
    tables: dict[str, int | str] = {}
    with sqlite3.connect(path) as connection:
        names = pd.read_sql_query("select name from sqlite_master where type='table'", connection)["name"].tolist()
        for name in names:
            try:
                tables[name] = int(pd.read_sql_query(f'select count(*) as n from "{name}"', connection)["n"].iloc[0])
            except Exception as exc:
                tables[name] = str(exc)
    return {"exists": True, "tables": tables}

def legacy_trade_range(bundle: MasterReadBundle) -> dict:
    if bundle.report.get("status") != "ok":
        return dict(bundle.report)
    frame = pd.DataFrame.from_records(bundle.source_rows)
    open_ts = pd.to_datetime(frame.get("horario_abertura"), utc=True, errors="coerce")
    close_ts = pd.to_datetime(frame.get("horario_fechamento"), utc=True, errors="coerce")
    combined = pd.concat([open_ts, close_ts]).dropna()
    return {
        **bundle.report,
        "min_trade_ts": combined.min().isoformat() if not combined.empty else None,
        "max_trade_ts": combined.max().isoformat() if not combined.empty else None,
    }

def main() -> None:
    legacy_bundle = read_trader_master_readonly(
        project_root=PROJECT_ROOT,
        trader_master_path=DEFAULT_MASTER,
    )
    backfill_features = table_info(
        Path("data/features/market_features_1m_backfill.parquet"),
        operational=True,
    )
    main_features = table_info(
        Path("data/features/market_features_60d.parquet"),
        operational=True,
    )
    affected = []
    for path, item in [
        ("data/features/market_features_1m_backfill.parquet", backfill_features),
        ("data/features/market_features_60d.parquet", main_features),
    ]:
        if item.get("lookahead_columns"):
            affected.append(
                {
                    "path": path,
                    "lookahead_columns": item.get("lookahead_columns", []),
                    "lookahead_columns_count": item.get("lookahead_columns_count", 0),
                }
            )
    report: dict[str, Any] = {
        "status": "warning" if affected else "ok",
        "reason": "operational_lookahead_columns_detected" if affected else "ok",
        "phase": "phase22_historical_market_backfill",
        "lookahead_columns_affected_paths": affected,
        "raw_files": [str(path) for path in Path("data/raw/binance_futures_klines").glob("*")],
        "backfill_features": backfill_features,
        "main_features": main_features,
        "trade_enriched": table_info(Path("data/features/trade_enriched.parquet")),
        "training_dataset": table_info(Path("data/features/training_dataset.parquet")),
        "legacy_master_readonly": legacy_trade_range(legacy_bundle),
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
