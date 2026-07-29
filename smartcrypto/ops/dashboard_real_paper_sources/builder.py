"""Read-only real paper data source builder for SMART FUTUROS dashboard."""

from __future__ import annotations

import math
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smartcrypto.runtime.integrity_traceability_v2 import (
    ConsistentReadError,
    atomic_write_json,
    read_json_consistent,
)


SCHEMA_VERSION = "dashboard_real_paper_sources_snapshot_v1"

DEFAULT_FREQTRADE_DB_SNAPSHOT = Path("data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite")
DEFAULT_NOTIFICATIONS_DB = Path("data/runtime/trade_event_notifications.sqlite")

DEFAULT_OUTPUT_PATH = Path("data/reports/dashboard_real_paper_sources_snapshot.json")

REPORT_PATHS = {
    "phase14_open_positions": Path("data/reports/phase14_open_positions_report.json"),
    "freqtrade_paper_db_snapshot_export": Path("data/reports/freqtrade_paper_db_snapshot_export.json"),
    "phase14_output_summary": Path("data/reports/phase14_output_summary.json"),
    "trade_event_notifications_report": Path("data/reports/trade_event_notifications_report.json"),
    "active_freqtrade_signals": Path("data/runtime/active_freqtrade_signals.json"),
    "qlib_fresh_prediction_runner": Path("data/reports/qlib_fresh_prediction_runner_report.json"),
    "phase13_signal_producer": Path("data/reports/phase13_signal_producer_report.json"),
}

SAFETY_FLAGS = {
    "dashboard_readonly": True,
    "paper_only": True,
    "shadow_only": True,
    "live_locked": True,
    "live_trading_enabled": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "uses_private_exchange": False,
    "uses_ccxt": False,
    "sends_orders": False,
    "sends_notifications": False,
    "changes_risk": False,
    "changes_model": False,
    "changes_config": False,
    "changes_active_signals": False,
}


@dataclass(frozen=True)
class BuildResult:
    """Result object returned by the real paper source builder."""

    exit_code: int
    snapshot: dict[str, Any]
    output_path: Path | None = None

    @property
    def status(self) -> str:
        return str(self.snapshot.get("status", "unknown"))


def utc_now_iso() -> str:
    """Return a deterministic UTC ISO timestamp shape used by dashboard reports."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_real_paper_sources_snapshot(
    *,
    project_root: str | Path = ".",
    output_path: str | Path | None = DEFAULT_OUTPUT_PATH,
    write: bool = True,
) -> BuildResult:
    """Build the SMART FUTUROS real paper data source snapshot.

    The builder reads only local JSON reports and SQLite snapshots in read-only mode.
    It never touches exchange adapters, runtime command buses, notification senders,
    risk mutation paths, model promotion paths, or configuration writers.
    """

    root = Path(project_root).resolve()
    resolved_output = root / Path(output_path) if output_path is not None else None

    source_paths = {
        "freqtrade_db_snapshot": DEFAULT_FREQTRADE_DB_SNAPSHOT.as_posix(),
        "notifications_db": DEFAULT_NOTIFICATIONS_DB.as_posix(),
        **{key: path.as_posix() for key, path in REPORT_PATHS.items()},
    }

    json_reports = {
        key: load_json_report(root / path)
        for key, path in REPORT_PATHS.items()
    }

    freqtrade_db = root / DEFAULT_FREQTRADE_DB_SNAPSHOT
    notifications_db = root / DEFAULT_NOTIFICATIONS_DB

    freqtrade = read_freqtrade_snapshot(freqtrade_db)
    notifications = read_notifications_snapshot(notifications_db)
    qlib = build_qlib_section(json_reports)
    phase14 = build_phase14_section(json_reports)
    source_health = build_source_health(root, source_paths)

    missing_required = []
    if not freqtrade_db.exists():
        missing_required.append(DEFAULT_FREQTRADE_DB_SNAPSHOT.as_posix())

    status = "ok"
    reason = "real_paper_sources_available"
    if missing_required:
        status = "blocked"
        reason = "missing_required_real_paper_sources"
    elif freqtrade.get("trades_total", 0) <= 0:
        status = "warning"
        reason = "freqtrade_snapshot_has_no_trades"

    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "created_at_utc": utc_now_iso(),
        "runtime_mode": "paper",
        **SAFETY_FLAGS,
        "source_paths": source_paths,
        "source_health": source_health,
        "freqtrade": freqtrade,
        "portfolio_risk": build_portfolio_risk_section(freqtrade),
        "grid_monitor": build_grid_monitor_section(freqtrade),
        "alerts_messaging": notifications,
        "qlib": qlib,
        "phase14": phase14,
        "audit": {
            "snapshot_source": "dashboard_real_paper_sources_v1",
            "dashboard_reads_only": True,
            "reads_sqlite_mode": "ro",
            "reads_json_reports_only": True,
            "writes_only_output_snapshot": bool(write and resolved_output is not None),
            "uses_private_exchange": False,
            "uses_ccxt": False,
            "sends_orders": False,
            "sends_notifications": False,
            "changes_risk": False,
            "changes_model": False,
            "changes_config": False,
        },
    }

    if write and resolved_output is not None:
        atomic_write_json(resolved_output, json_safe(snapshot))

    exit_code = 2 if status == "blocked" else 0
    return BuildResult(exit_code=exit_code, snapshot=json_safe(snapshot), output_path=resolved_output)


def load_json_report(path: Path) -> dict[str, Any]:
    """Load one JSON report safely."""

    if not path.exists():
        return {
            "exists": False,
            "path": path.as_posix(),
            "status": "missing",
            "error": "file_not_found",
        }

    try:
        payload = read_json_consistent(path)
    except ConsistentReadError as exc:
        return {
            "exists": True,
            "path": path.as_posix(),
            "status": "error",
            "error": f"{type(exc).__name__}:{exc.reason}",
            "size_bytes": path.stat().st_size,
        }

    if isinstance(payload, dict):
        return {
            "exists": True,
            "path": path.as_posix(),
            "status": "ok",
            "size_bytes": path.stat().st_size,
            "payload": payload,
        }

    return {
        "exists": True,
        "path": path.as_posix(),
        "status": "warning",
        "error": "json_root_is_not_object",
        "size_bytes": path.stat().st_size,
        "payload": payload,
    }


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    """Open SQLite database in explicit read-only URI mode."""

    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "select 1 from sqlite_master where type='table' and name=? limit 1",
        (table,),
    ).fetchone()
    return row is not None


def fetch_rows(connection: sqlite3.Connection, table: str, *, order_by: str | None = None) -> list[dict[str, Any]]:
    if not table_exists(connection, table):
        return []

    sql = f'select * from "{table}"'
    if order_by:
        sql += f" order by {order_by}"
    return [dict(row) for row in connection.execute(sql).fetchall()]


def read_freqtrade_snapshot(db_path: Path) -> dict[str, Any]:
    """Read Freqtrade paper database snapshot and calculate dashboard metrics."""

    if not db_path.exists() or db_path.stat().st_size <= 0:
        return {
            "status": "blocked",
            "reason": "freqtrade_db_snapshot_missing_or_empty",
            "db_path": db_path.as_posix(),
            "db_exists": db_path.exists(),
            "db_size_bytes": db_path.stat().st_size if db_path.exists() else 0,
            "trades_total": 0,
            "orders_total": 0,
            "open_trades": 0,
            "closed_trades": 0,
        }

    with connect_readonly(db_path) as connection:
        trades = fetch_rows(connection, "trades", order_by="id asc")
        orders = fetch_rows(connection, "orders", order_by="id asc")
        pairlocks = fetch_rows(connection, "pairlocks", order_by="id asc")
        key_values = fetch_rows(connection, "KeyValueStore", order_by="id asc")

    closed_trades = [row for row in trades if not bool(row.get("is_open"))]
    open_trades = [row for row in trades if bool(row.get("is_open"))]
    pairs = sorted({str(row.get("pair")) for row in trades if row.get("pair")})
    pair_counter = Counter(str(row.get("pair")) for row in trades if row.get("pair"))
    dominant_pair = pair_counter.most_common(1)[0][0] if pair_counter else None

    side_counter = Counter(trade_side(row) for row in trades)
    strategy_counter = Counter(str(row.get("strategy")) for row in trades if row.get("strategy"))

    realized_pnl_abs = sum_float(row.get("close_profit_abs") for row in closed_trades)
    realized_profit_ratio = sum_float(row.get("close_profit") for row in closed_trades)
    fees_total = sum_float(
        safe_float(row.get("fee_open_cost")) + safe_float(row.get("fee_close_cost"))
        for row in closed_trades
    )
    open_exposure_usdt = sum_float(row.get("stake_amount") for row in open_trades)
    closed_stake_total = sum_float(row.get("stake_amount") for row in closed_trades)
    win_count = sum(1 for row in closed_trades if safe_float(row.get("close_profit_abs")) > 0)
    loss_count = sum(1 for row in closed_trades if safe_float(row.get("close_profit_abs")) < 0)
    win_rate = ratio_pct(win_count, len(closed_trades))

    equity_curve = build_equity_curve(closed_trades)
    drawdown = calculate_drawdown(equity_curve, denominator=closed_stake_total)

    latest_trades = [
        trade_projection(row)
        for row in sorted(trades, key=lambda item: sortable_datetime(item.get("close_date") or item.get("open_date")), reverse=True)[:20]
    ]

    latest_orders = [
        order_projection(row)
        for row in sorted(orders, key=lambda item: sortable_datetime(item.get("order_update_date") or item.get("order_date")), reverse=True)[:20]
    ]

    bot_start_time = None
    for row in key_values:
        if row.get("key") == "bot_start_time":
            bot_start_time = row.get("datetime_value") or row.get("string_value")
            break

    return {
        "status": "ok",
        "reason": "freqtrade_db_snapshot_loaded",
        "db_path": db_path.as_posix(),
        "db_exists": True,
        "db_size_bytes": db_path.stat().st_size,
        "bot_start_time": bot_start_time,
        "tables": {
            "trades": len(trades),
            "orders": len(orders),
            "pairlocks": len(pairlocks),
            "key_value_store": len(key_values),
        },
        "trades_total": len(trades),
        "orders_total": len(orders),
        "pairlocks_total": len(pairlocks),
        "open_trades": len(open_trades),
        "closed_trades": len(closed_trades),
        "pairs": pairs,
        "pair_counts": dict(sorted(pair_counter.items())),
        "dominant_pair": dominant_pair,
        "side_counts": dict(sorted(side_counter.items())),
        "strategy_counts": dict(sorted(strategy_counter.items())),
        "realized_pnl_abs": round(realized_pnl_abs, 8),
        "realized_profit_ratio_sum": round(realized_profit_ratio, 8),
        "fees_total": round(fees_total, 8),
        "open_exposure_usdt": round(open_exposure_usdt, 8),
        "closed_stake_total": round(closed_stake_total, 8),
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate": round(win_rate, 4),
        "max_drawdown_abs": drawdown["max_drawdown_abs"],
        "max_drawdown_pct": drawdown["max_drawdown_pct"],
        "latest_trades": latest_trades,
        "open_positions": [trade_projection(row) for row in open_trades],
        "latest_orders": latest_orders,
        "order_side_counts": dict(sorted(Counter(str(row.get("side")) for row in orders if row.get("side")).items())),
        "order_status_counts": dict(sorted(Counter(str(row.get("status")) for row in orders if row.get("status")).items())),
        "exit_reason_counts": dict(sorted(Counter(str(row.get("exit_reason")) for row in closed_trades if row.get("exit_reason")).items())),
    }


def read_notifications_snapshot(db_path: Path) -> dict[str, Any]:
    """Read notification state database in read-only mode."""

    if not db_path.exists() or db_path.stat().st_size <= 0:
        return {
            "status": "warning",
            "reason": "notifications_db_missing_or_empty",
            "db_path": db_path.as_posix(),
            "events_total": 0,
            "channels_total": 0,
            "latest_events": [],
        }

    with connect_readonly(db_path) as connection:
        events = fetch_rows(connection, "trade_event_notifications", order_by="created_at_utc asc")
        channels = fetch_rows(connection, "trade_event_notification_channels", order_by="created_at_utc asc")

    event_status_counts = Counter(str(row.get("status")) for row in events if row.get("status"))
    channel_status_counts = Counter(str(row.get("status")) for row in channels if row.get("status"))
    channel_counts = Counter(str(row.get("channel")) for row in channels if row.get("channel"))
    event_type_counts = Counter(str(row.get("event_type")) for row in events if row.get("event_type"))

    latest_events = [
        notification_projection(row)
        for row in sorted(events, key=lambda item: sortable_datetime(item.get("created_at_utc") or item.get("event_time_utc")), reverse=True)[:20]
    ]

    return {
        "status": "ok",
        "reason": "notifications_db_loaded",
        "db_path": db_path.as_posix(),
        "db_exists": True,
        "db_size_bytes": db_path.stat().st_size,
        "events_total": len(events),
        "channels_total": len(channels),
        "event_status_counts": dict(sorted(event_status_counts.items())),
        "channel_status_counts": dict(sorted(channel_status_counts.items())),
        "channel_counts": dict(sorted(channel_counts.items())),
        "event_type_counts": dict(sorted(event_type_counts.items())),
        "sent_total": event_status_counts.get("sent", 0),
        "failed_channel_total": channel_status_counts.get("failed", 0),
        "pending_total": event_status_counts.get("pending", 0),
        "latest_events": latest_events,
    }


def build_portfolio_risk_section(freqtrade: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": freqtrade.get("status", "unknown"),
        "source": "freqtrade_paper_sqlite_snapshot",
        "open_positions_count": freqtrade.get("open_trades", 0),
        "closed_trades_count": freqtrade.get("closed_trades", 0),
        "total_trades_count": freqtrade.get("trades_total", 0),
        "realized_pnl_abs": freqtrade.get("realized_pnl_abs", 0.0),
        "fees_total": freqtrade.get("fees_total", 0.0),
        "open_exposure_usdt": freqtrade.get("open_exposure_usdt", 0.0),
        "closed_stake_total": freqtrade.get("closed_stake_total", 0.0),
        "win_rate": freqtrade.get("win_rate", 0.0),
        "max_drawdown_abs": freqtrade.get("max_drawdown_abs", 0.0),
        "max_drawdown_pct": freqtrade.get("max_drawdown_pct", 0.0),
        "dominant_pair": freqtrade.get("dominant_pair"),
        "pair_counts": freqtrade.get("pair_counts", {}),
        "side_counts": freqtrade.get("side_counts", {}),
    }


def build_grid_monitor_section(freqtrade: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": freqtrade.get("status", "unknown"),
        "source": "freqtrade_paper_orders_and_trades",
        "dominant_pair": freqtrade.get("dominant_pair"),
        "orders_total": freqtrade.get("orders_total", 0),
        "trades_total": freqtrade.get("trades_total", 0),
        "open_trades": freqtrade.get("open_trades", 0),
        "latest_orders": freqtrade.get("latest_orders", []),
        "latest_trades": freqtrade.get("latest_trades", []),
        "order_side_counts": freqtrade.get("order_side_counts", {}),
        "order_status_counts": freqtrade.get("order_status_counts", {}),
        "exit_reason_counts": freqtrade.get("exit_reason_counts", {}),
    }


def build_qlib_section(json_reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    qlib_report = json_payload(json_reports, "qlib_fresh_prediction_runner")
    signal_report = json_payload(json_reports, "phase13_signal_producer")
    active_signals = json_payload(json_reports, "active_freqtrade_signals")

    return {
        "status": qlib_report.get("status", "unknown"),
        "reason": qlib_report.get("reason"),
        "model_version": qlib_report.get("model_version") or active_signals.get("model_version"),
        "timeframe": qlib_report.get("timeframe"),
        "prediction_rows": qlib_report.get("rows") or signal_report.get("prediction_rows"),
        "input_data_status": qlib_report.get("input_data_status"),
        "input_data_age_minutes": qlib_report.get("input_data_age_minutes"),
        "signals_after": signal_report.get("signals_after"),
        "signals_count": len(active_signals.get("signals", [])) if isinstance(active_signals.get("signals"), list) else 0,
        "pairs": signal_report.get("pairs") or qlib_report.get("pairs") or [],
        "sides": signal_report.get("sides") or [],
        "valid_until_min": signal_report.get("valid_until_min"),
        "valid_until_max": signal_report.get("valid_until_max"),
        "shadow_only": bool(qlib_report.get("shadow_only", True)),
        "runtime_mode": qlib_report.get("runtime_mode", "paper"),
    }


def build_phase14_section(json_reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    report = json_payload(json_reports, "phase14_open_positions")
    export = json_payload(json_reports, "freqtrade_paper_db_snapshot_export")
    summary = json_payload(json_reports, "phase14_output_summary")

    return {
        "status": report.get("status", "unknown"),
        "reason": report.get("reason"),
        "rows": report.get("rows"),
        "open_rows": report.get("open_rows"),
        "closed_rows": report.get("closed_rows"),
        "max_open_trades": report.get("max_open_trades"),
        "saturated": report.get("saturated"),
        "expected_pairs": report.get("expected_pairs", []),
        "open_pairs": report.get("open_pairs", []),
        "recent": report.get("recent", []),
        "created_at": report.get("created_at"),
        "db_snapshot_used": report.get("db_snapshot_used"),
        "db_snapshot_output": export.get("output"),
        "db_snapshot_output_size_bytes": export.get("output_size_bytes"),
        "phase14_status": summary.get("phase14_status", {}),
    }


def build_source_health(root: Path, source_paths: dict[str, str]) -> dict[str, Any]:
    sources = []
    missing = []

    for key, relative in source_paths.items():
        path = root / relative
        exists = path.exists()
        if not exists and key in {"freqtrade_db_snapshot"}:
            missing.append(relative)
        sources.append(
            {
                "key": key,
                "path": relative,
                "exists": exists,
                "size_bytes": path.stat().st_size if exists and path.is_file() else 0,
                "last_modified_utc": file_mtime_iso(path) if exists and path.is_file() else None,
            }
        )

    return {
        "status": "blocked" if missing else "ok",
        "missing_required_sources": missing,
        "sources": sources,
    }


def json_payload(json_reports: dict[str, dict[str, Any]], key: str) -> dict[str, Any]:
    report = json_reports.get(key, {})
    payload = report.get("payload", {})
    return payload if isinstance(payload, dict) else {}


def trade_projection(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "pair": row.get("pair"),
        "side": trade_side(row),
        "is_open": bool(row.get("is_open")),
        "open_date": row.get("open_date"),
        "close_date": row.get("close_date"),
        "open_rate": rounded(row.get("open_rate")),
        "close_rate": rounded(row.get("close_rate")),
        "stake_amount": rounded(row.get("stake_amount")),
        "amount": rounded(row.get("amount")),
        "realized_profit": rounded(row.get("realized_profit")),
        "close_profit": rounded(row.get("close_profit")),
        "close_profit_abs": rounded(row.get("close_profit_abs")),
        "leverage": rounded(row.get("leverage")),
        "exit_reason": row.get("exit_reason"),
        "strategy": row.get("strategy"),
        "enter_tag": row.get("enter_tag"),
        "timeframe": row.get("timeframe"),
    }


def order_projection(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "trade_id": row.get("ft_trade_id"),
        "pair": row.get("ft_pair") or row.get("symbol"),
        "side": row.get("side") or row.get("ft_order_side"),
        "status": row.get("status"),
        "order_type": row.get("order_type"),
        "price": rounded(row.get("price") or row.get("ft_price")),
        "average": rounded(row.get("average")),
        "amount": rounded(row.get("amount") or row.get("ft_amount")),
        "filled": rounded(row.get("filled")),
        "remaining": rounded(row.get("remaining")),
        "cost": rounded(row.get("cost")),
        "order_date": row.get("order_date"),
        "order_filled_date": row.get("order_filled_date"),
        "order_update_date": row.get("order_update_date"),
        "tag": row.get("ft_order_tag"),
    }


def notification_projection(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "notification_key": row.get("notification_key"),
        "trade_id": row.get("trade_id"),
        "event_type": row.get("event_type"),
        "pair": row.get("pair"),
        "side": row.get("side"),
        "event_time_utc": row.get("event_time_utc"),
        "status": row.get("status"),
        "dry_run": bool(row.get("dry_run")),
        "created_at_utc": row.get("created_at_utc"),
    }


def build_equity_curve(closed_trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    equity = 0.0
    output = []

    ordered = sorted(
        closed_trades,
        key=lambda row: sortable_datetime(row.get("close_date") or row.get("open_date")),
    )

    for row in ordered:
        equity += safe_float(row.get("close_profit_abs"))
        output.append(
            {
                "trade_id": row.get("id"),
                "timestamp": row.get("close_date") or row.get("open_date"),
                "pnl_abs": rounded(row.get("close_profit_abs")),
                "equity_abs": round(equity, 8),
            }
        )

    return output


def calculate_drawdown(equity_curve: list[dict[str, Any]], *, denominator: float) -> dict[str, float]:
    peak = 0.0
    max_drawdown = 0.0

    for point in equity_curve:
        equity = safe_float(point.get("equity_abs"))
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)

    pct = ratio_pct(max_drawdown, denominator)
    return {
        "max_drawdown_abs": round(max_drawdown, 8),
        "max_drawdown_pct": round(pct, 4),
    }


def trade_side(row: dict[str, Any]) -> str:
    return "SHORT" if bool(row.get("is_short")) else "LONG"


def sortable_datetime(value: Any) -> str:
    return str(value or "")


def safe_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return number


def sum_float(values: Any) -> float:
    return sum(safe_float(value) for value in values)


def ratio_pct(numerator: float | int, denominator: float | int) -> float:
    denom = safe_float(denominator)
    if denom == 0:
        return 0.0
    return safe_float(numerator) / denom * 100.0


def rounded(value: Any, digits: int = 8) -> float | None:
    if value is None:
        return None
    return round(safe_float(value), digits)


def file_mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return value
    return value
