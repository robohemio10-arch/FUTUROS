from __future__ import annotations

import json
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


@dataclass(frozen=True)
class ForceCloseConfig:
    db_candidates: list[Path]
    market_features_path: Path
    report_path: Path
    allowed_pairs: set[str]
    backup_before_write: bool
    exit_reason: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_sqlite() -> str:
    return utc_now().replace(tzinfo=None).isoformat(sep=" ")


def read_config(path: str | Path = "config/paper_force_close.yml") -> ForceCloseConfig:
    config_path = Path(path)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    policy = data.get("policy", {})
    paths = data.get("paths", {})
    risk = data.get("risk", {})

    return ForceCloseConfig(
        db_candidates=[Path(item) for item in paths.get("freqtrade_db_candidates", [])],
        market_features_path=Path(paths.get("market_features", "data/features/market_features_60d.parquet")),
        report_path=Path(paths.get("report", "data/reports/phase16_force_close_report.json")),
        allowed_pairs=set(risk.get("allowed_pairs", [])),
        backup_before_write=bool(policy.get("backup_sqlite_before_write", True)),
        exit_reason=str(policy.get("exit_reason", "phase16_controlled_sqlite_paper_force_close")),
    )


def assert_paper_only() -> None:
    runtime_mode = os.getenv("SMARTCRYPTO_RUNTIME_MODE", "paper").lower()
    live_enabled = os.getenv("LIVE_ENABLED", "false").lower()
    order_submission = os.getenv("ORDER_SUBMISSION_ENABLED", "false").lower()
    real_order_submission = os.getenv("REAL_ORDER_SUBMISSION_ENABLED", "false").lower()

    unsafe = [
        runtime_mode not in {"paper", "dry_run", "dry-run", "test"},
        live_enabled == "true",
        order_submission == "true",
        real_order_submission == "true",
    ]

    if any(unsafe):
        raise RuntimeError(
            "Phase 16 is allowed only in paper/dry-run mode. "
            f"runtime_mode={runtime_mode}, live_enabled={live_enabled}, "
            f"order_submission={order_submission}, real_order_submission={real_order_submission}"
        )


def find_db_path(candidates: list[Path]) -> Path | None:
    for path in candidates:
        if path.exists():
            return path
    return None


def table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "select name from sqlite_master where type='table' and name=?",
        (table,),
    ).fetchone()
    return row is not None


def read_open_trades(connection: sqlite3.Connection, allowed_pairs: set[str], pair: str) -> pd.DataFrame:
    if not table_exists(connection, "trades"):
        return pd.DataFrame()

    frame = pd.read_sql_query("select * from trades where is_open = 1 order by id", connection)
    if frame.empty:
        return frame

    if allowed_pairs:
        frame = frame[frame["pair"].isin(allowed_pairs)]

    if pair and pair.lower() != "all":
        frame = frame[frame["pair"].eq(pair)]

    return frame.reset_index(drop=True)


def pair_to_symbol(pair: str) -> str:
    return pair.replace("/USDT:USDT", "USDT").replace("/", "").replace(":USDT", "")


def latest_market_close(features_path: Path, pair: str) -> float | None:
    if not features_path.exists():
        return None

    symbol = pair_to_symbol(pair)
    try:
        frame = pd.read_parquet(features_path, columns=["symbol", "tf", "ts", "close"])
    except Exception:
        frame = pd.read_parquet(features_path)

    if "tf" in frame.columns:
        frame = frame[frame["tf"].astype(str).eq("5m")]

    if "symbol" in frame.columns:
        frame = frame[frame["symbol"].astype(str).eq(symbol)]

    if frame.empty:
        return None

    if "ts" in frame.columns:
        frame = frame.sort_values("ts")

    value = frame["close"].dropna().iloc[-1]
    return float(value)


def fallback_close_rate(row: pd.Series, features_path: Path) -> float:
    market_rate = latest_market_close(features_path, str(row["pair"]))
    if market_rate and market_rate > 0:
        return market_rate

    open_rate = float(row.get("open_rate") or 0)
    if open_rate <= 0:
        return 1.0

    return open_rate


def compute_profit(row: pd.Series, close_rate: float) -> tuple[float, float]:
    open_rate = float(row.get("open_rate") or 0)
    if open_rate <= 0:
        return 0.0, 0.0

    leverage = float(row.get("leverage") or 1.0)
    stake_amount = float(row.get("stake_amount") or row.get("max_stake_amount") or 0.0)
    is_short = int(row.get("is_short") or 0) == 1

    if is_short:
        raw_profit = (open_rate - close_rate) / open_rate
    else:
        raw_profit = (close_rate - open_rate) / open_rate

    close_profit = raw_profit * leverage
    close_profit_abs = stake_amount * close_profit
    return float(close_profit), float(close_profit_abs)


def backup_db(db_path: Path) -> Path:
    stamp = utc_now().strftime("%Y%m%d_%H%M%S")
    backup_dir = Path("data/backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{db_path.name}.phase16_{stamp}.bak"
    shutil.copy2(db_path, backup_path)
    return backup_path


def update_trade(connection: sqlite3.Connection, row: pd.Series, close_rate: float, exit_reason: str) -> dict[str, Any]:
    close_profit, close_profit_abs = compute_profit(row, close_rate)
    trade_id = int(row["id"])
    close_date = utc_now_sqlite()

    available_columns = {
        item[1] for item in connection.execute("pragma table_info(trades)").fetchall()
    }

    updates = {
        "is_open": 0,
        "close_rate": close_rate,
        "close_rate_requested": close_rate,
        "close_date": close_date,
        "realized_profit": close_profit_abs,
        "close_profit": close_profit,
        "close_profit_abs": close_profit_abs,
        "exit_reason": exit_reason,
        "exit_order_status": "closed",
    }

    filtered = {key: value for key, value in updates.items() if key in available_columns}
    set_clause = ", ".join(f"{key} = ?" for key in filtered)
    values = list(filtered.values()) + [trade_id]

    connection.execute(f"update trades set {set_clause} where id = ?", values)

    return {
        "id": trade_id,
        "pair": row.get("pair"),
        "is_short": bool(row.get("is_short")),
        "open_rate": float(row.get("open_rate") or 0.0),
        "close_rate": close_rate,
        "close_profit": close_profit,
        "close_profit_abs": close_profit_abs,
        "exit_reason": exit_reason,
        "close_date": close_date,
    }


def export_raw(connection: sqlite3.Connection, output_path: Path = Path("data/trades/freqtrade_paper_trades_raw.parquet")) -> int:
    if not table_exists(connection, "trades"):
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.read_sql_query("select * from trades order by id", connection)
    frame.to_parquet(output_path, index=False)
    return len(frame)


def force_close_open_paper_trades(pair: str = "all", config_path: str | Path = "config/paper_force_close.yml") -> dict[str, Any]:
    assert_paper_only()
    config = read_config(config_path)
    db_path = find_db_path(config.db_candidates)

    report: dict[str, Any] = {
        "status": "blocked",
        "reason": None,
        "pair": pair,
        "db_path": str(db_path) if db_path else None,
        "backup_path": None,
        "open_before": 0,
        "closed_by_phase16": 0,
        "raw_rows_after": 0,
        "closed_trades": [],
        "created_at": utc_now().isoformat(),
    }

    if db_path is None:
        report["reason"] = "freqtrade_db_not_found"
        write_report(config.report_path, report)
        return report

    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=rw", uri=True)
    except sqlite3.OperationalError as error:
        report["status"] = "blocked"
        report["reason"] = f"sqlite_open_failed:{error}"
        write_report(config.report_path, report)
        return report

    try:
        open_trades = read_open_trades(connection, config.allowed_pairs, pair)
        report["open_before"] = int(len(open_trades))

        if open_trades.empty:
            report["status"] = "waiting"
            report["reason"] = "no_open_trades"
            report["raw_rows_after"] = export_raw(connection)
            write_report(config.report_path, report)
            return report

        if config.backup_before_write:
            report["backup_path"] = str(backup_db(db_path))

        closed = []
        for _, row in open_trades.iterrows():
            close_rate = fallback_close_rate(row, config.market_features_path)
            closed.append(update_trade(connection, row, close_rate, config.exit_reason))

        connection.commit()
        report["status"] = "ok"
        report["reason"] = None
        report["closed_by_phase16"] = len(closed)
        report["closed_trades"] = closed
        report["raw_rows_after"] = export_raw(connection)
    except sqlite3.OperationalError as error:
        connection.rollback()
        report["status"] = "blocked"
        report["reason"] = f"sqlite_write_failed:{error}"
        report["raw_rows_after"] = export_raw(connection)
    finally:
        connection.close()

    write_report(config.report_path, report)
    return report


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
