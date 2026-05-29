from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.data.paper_trade_lifecycle import PaperTradeLifecycleError, read_trades


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def first_existing_path(paths: list[str] | tuple[str, ...]) -> Path | None:
    for value in paths:
        candidate = Path(value)
        if candidate.exists():
            return candidate
    return None


def load_freqtrade_trades_snapshot(paths: list[str] | tuple[str, ...]) -> dict[str, Any]:
    db_path = first_existing_path(paths)
    if db_path is None:
        return {
            "status": "missing",
            "error": "freqtrade_db_not_found",
            "db_candidates": [str(item) for item in paths],
            "db_path": None,
            "db_snapshot_used": False,
            "db_last_read_at": utc_now(),
            "trades": pd.DataFrame(),
        }
    try:
        trades = read_trades(db_path, use_snapshot=True)
        if "id" in trades.columns:
            trades = trades.sort_values("id", ascending=False, kind="stable").reset_index(drop=True)
        return {
            "status": "ok",
            "error": None,
            "db_candidates": [str(item) for item in paths],
            "db_path": str(db_path),
            "db_snapshot_used": True,
            "db_last_read_at": utc_now(),
            "trades": trades,
        }
    except PaperTradeLifecycleError as exc:
        return {
            "status": "error",
            "error": str(exc),
            "db_candidates": [str(item) for item in paths],
            "db_path": str(db_path),
            "db_snapshot_used": True,
            "db_last_read_at": utc_now(),
            "trades": pd.DataFrame(),
        }


def perf_metrics(trades: pd.DataFrame) -> dict[str, Any]:
    if trades.empty:
        return {
            "trades": 0,
            "closed": 0,
            "open": 0,
            "pnl": 0.0,
            "win_rate": None,
            "profit_factor": None,
            "max_drawdown": None,
        }
    is_open = trades.get("is_open", pd.Series([0] * len(trades), index=trades.index)).fillna(0).astype(int)
    closed = trades.loc[is_open == 0].copy()
    open_rows = int((is_open == 1).sum())
    pnl_col = resolve_pnl_column(closed)
    if pnl_col is None:
        return {
            "trades": int(len(trades)),
            "closed": int(len(closed)),
            "open": open_rows,
            "pnl": 0.0,
            "win_rate": None,
            "profit_factor": None,
            "max_drawdown": None,
        }
    pnl = pd.to_numeric(closed[pnl_col], errors="coerce").fillna(0.0).astype(float)
    gains = pnl[pnl > 0].sum()
    losses = abs(pnl[pnl < 0].sum())
    curve = pnl.cumsum()
    drawdown = (curve.cummax() - curve).max() if len(curve) else 0.0
    return {
        "trades": int(len(trades)),
        "closed": int(len(closed)),
        "open": open_rows,
        "pnl": float(pnl.sum()),
        "win_rate": float((pnl > 0).mean()) if len(pnl) else None,
        "profit_factor": float(gains / losses) if losses > 0 else None,
        "max_drawdown": float(drawdown) if len(curve) else None,
    }


def resolve_pnl_column(frame: pd.DataFrame) -> str | None:
    for column in ("close_profit_abs", "realized_profit"):
        if column in frame.columns:
            return column
    return None


def status_payload(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": state.get("status"),
        "error": state.get("error"),
        "db_path": state.get("db_path"),
        "db_snapshot_used": state.get("db_snapshot_used"),
        "db_last_read_at": state.get("db_last_read_at"),
    }
