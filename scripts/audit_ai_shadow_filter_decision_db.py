from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


DEFAULT_SQLITE = "data/runtime/ai_shadow_filter_decisions.sqlite"
DEFAULT_DATASET = "data/features/training_dataset_quality_gated_binance_1m.parquet"
DEFAULT_SUMMARY = "data/reports/ai_shadow_filter_decision_db_audit_summary.json"
DEFAULT_BY_SYMBOL = "data/reports/ai_shadow_filter_decision_db_by_symbol.csv"
DEFAULT_BY_DAY = "data/reports/ai_shadow_filter_decision_db_by_day.csv"


def json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [json_safe(v) for v in obj]
    if isinstance(obj, tuple):
        return [json_safe(v) for v in obj]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        if np.isnan(obj) or np.isinf(obj):
            return None
        return float(obj)
    return obj


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(r[1]) for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]


def list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return [str(r[0]) for r in rows]


def discover_table(conn: sqlite3.Connection) -> str:
    tables = list_tables(conn)

    for table in ["ai_shadow_decisions", "ai_shadow_filter_decisions", "shadow_decisions", "decisions"]:
        if table in tables:
            return table

    scored = []
    for table in tables:
        cols = set(table_columns(conn, table))
        row_count = int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        score = 0
        if "trade_id" in cols:
            score += 10
        if "logged_at_utc" in cols:
            score += 4
        if any(c in cols for c in ["decision", "ai_decision", "shadow_decision"]):
            score += 5
        scored.append((score, row_count, table))

    scored.sort(reverse=True)
    if scored and scored[0][0] > 0:
        return scored[0][2]

    raise RuntimeError("Nenhuma tabela IA Shadow encontrada.")


def pick_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def max_drawdown(pnl: pd.Series) -> float:
    if pnl.empty:
        return 0.0
    cumulative = pnl.fillna(0).cumsum()
    return float((cumulative - cumulative.cummax()).min())


def policy_metrics(df: pd.DataFrame, pnl_col: str) -> dict[str, Any]:
    pnl = pd.to_numeric(df[pnl_col], errors="coerce").fillna(0.0)
    gross_profit = float(pnl[pnl > 0].sum())
    gross_loss = float(pnl[pnl < 0].sum())
    pf = gross_profit / abs(gross_loss) if gross_loss < 0 else None

    return {
        "net_pnl_usdt": float(pnl.sum()),
        "profit_factor": pf,
        "max_drawdown_usdt": max_drawdown(pnl),
        "win_rate": float((pnl > 0).mean()) if len(pnl) else None,
    }


def enrich_from_dataset(decisions: pd.DataFrame, dataset_path: Path) -> pd.DataFrame:
    if "trade_id" not in decisions.columns or not dataset_path.exists():
        return decisions

    ds = pd.read_parquet(dataset_path)

    keep = [
        c for c in [
            "trade_id",
            "reported_pnl_usdt",
            "symbol",
            "side",
            "open_time_utc",
            "target_win",
            "source_file",
        ]
        if c in ds.columns
    ]

    if "trade_id" not in keep:
        return decisions

    ds = ds[keep].copy()
    merged = decisions.merge(ds, on="trade_id", how="left", suffixes=("", "_dataset"))

    for col in ["reported_pnl_usdt", "symbol", "side", "open_time_utc", "target_win", "source_file"]:
        ds_col = f"{col}_dataset"
        if ds_col in merged.columns:
            if col not in merged.columns:
                merged[col] = merged[ds_col]
            else:
                merged[col] = merged[col].where(merged[col].notna() & merged[col].astype(str).ne(""), merged[ds_col])

    return merged


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", default=DEFAULT_SQLITE)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--summary-json", default=DEFAULT_SUMMARY)
    parser.add_argument("--by-symbol-csv", default=DEFAULT_BY_SYMBOL)
    parser.add_argument("--by-day-csv", default=DEFAULT_BY_DAY)
    args = parser.parse_args()

    sqlite_path = Path(args.sqlite)
    dataset_path = Path(args.dataset)
    summary_path = Path(args.summary_json)
    by_symbol_path = Path(args.by_symbol_csv)
    by_day_path = Path(args.by_day_csv)

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    by_symbol_path.parent.mkdir(parents=True, exist_ok=True)
    by_day_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(sqlite_path) as conn:
        table = discover_table(conn)
        df = pd.read_sql_query(f'SELECT * FROM "{table}"', conn)

    if df.empty:
        raise RuntimeError(f"Tabela {table} vazia.")

    df = enrich_from_dataset(df, dataset_path)

    decision_col = pick_col(df, ["decision", "ai_decision", "shadow_decision"])
    symbol_col = pick_col(df, ["symbol", "symbol_dataset", "pair"])
    time_col = pick_col(df, ["open_time_utc", "trade_time", "timestamp", "logged_at_utc", "created_at_utc"])
    pnl_col = pick_col(df, ["reported_pnl_usdt", "pnl_usdt", "pnl", "reported_pnl_usdt_dataset"])

    if decision_col is None:
        raise RuntimeError("Tabela sem coluna de decis├úo.")

    if symbol_col is None:
        raise RuntimeError("Tabela sem coluna de s├¡mbolo mesmo ap├│s merge com dataset.")

    if pnl_col is None:
        raise RuntimeError("Sem coluna de PnL no SQLite e sem PnL recuper├ível via dataset.")

    trade_time = pd.to_datetime(df[time_col], errors="coerce", utc=True) if time_col else pd.Series(pd.NaT, index=df.index)
    df["_trade_time"] = trade_time
    df["_trade_day"] = trade_time.dt.strftime("%Y-%m-%d")
    df["_decision"] = df[decision_col].astype(str)
    df["_pnl"] = pd.to_numeric(df[pnl_col], errors="coerce").fillna(0.0)

    accepted = df[df["_decision"].eq("AI_ACCEPT")].copy()
    rejected = df[df["_decision"].eq("AI_REJECT")].copy()

    by_symbol = (
        df.groupby(symbol_col)
        .agg(
            rows=("_decision", "count"),
            accepted=("_decision", lambda s: int((s == "AI_ACCEPT").sum())),
            rejected=("_decision", lambda s: int((s == "AI_REJECT").sum())),
            net_pnl_usdt=("_pnl", "sum"),
        )
        .reset_index()
    )
    by_symbol.to_csv(by_symbol_path, index=False, encoding="utf-8-sig")

    by_day = (
        df.groupby("_trade_day")
        .agg(
            rows=("_decision", "count"),
            accepted=("_decision", lambda s: int((s == "AI_ACCEPT").sum())),
            rejected=("_decision", lambda s: int((s == "AI_REJECT").sum())),
            net_pnl_usdt=("_pnl", "sum"),
        )
        .reset_index()
    )
    by_day.to_csv(by_day_path, index=False, encoding="utf-8-sig")

    base = policy_metrics(df, "_pnl")
    shadow = policy_metrics(accepted, "_pnl")

    summary = {
        "status": "ok",
        "mode": "ai_shadow_filter_sqlite_audit",
        "sqlite": str(sqlite_path),
        "sqlite_table": table,
        "dataset_used_for_pnl_join": str(dataset_path),
        "rows": int(len(df)),
        "first_trade_time": trade_time.min().isoformat() if trade_time.notna().any() else None,
        "last_trade_time": trade_time.max().isoformat() if trade_time.notna().any() else None,
        "decision_counts": df["_decision"].value_counts(dropna=False).to_dict(),
        "symbol_counts": df[symbol_col].astype(str).value_counts(dropna=False).to_dict(),
        "acceptance_rate": float(len(accepted) / len(df)) if len(df) else None,
        "base_policy": base,
        "shadow_filtered": {
            **shadow,
            "accepted_trades": int(len(accepted)),
            "rejected_trades": int(len(rejected)),
        },
        "delta_vs_base": {
            "net_pnl_delta_usdt": float(shadow["net_pnl_usdt"] - base["net_pnl_usdt"]),
            "max_drawdown_delta_usdt": float(shadow["max_drawdown_usdt"] - base["max_drawdown_usdt"]),
        },
        "safety": {
            "contains_order_execution_columns": any(
                c.lower() in {"order", "execute", "send_order", "order_id_to_execute"}
                for c in df.columns
            ),
            "sends_orders": False,
            "changes_risk": False,
        },
        "outputs": {
            "summary_json": str(summary_path),
            "by_symbol_csv": str(by_symbol_path),
            "by_day_csv": str(by_day_path),
        },
    }

    summary_path.write_text(json.dumps(json_safe(summary), indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(json_safe(summary), indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
