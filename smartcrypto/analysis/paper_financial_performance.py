from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


OK = "ok"
BLOCKED = "blocked"
MISSING_SOURCE = "missing_source"
MISSING_PNL_COLUMN = "missing_pnl_column"
MISSING_TIMESTAMP_COLUMN = "missing_timestamp_column"
INVALID_SCHEMA = "invalid_schema"
INSUFFICIENT_SAMPLE = "insufficient_sample"

DEFAULT_REPORT_PATH = Path("data/reports/paper_financial_performance_metrics_report.json")
DEFAULT_SOURCE_CANDIDATES = (
    Path("data/features/trade_enriched.parquet"),
    Path("data/features/training_dataset.parquet"),
    Path("data/features/training_dataset_quality_gated_binance_1m.parquet"),
)

PNL_COLUMN_CANDIDATES = (
    "reported_pnl_usdt",
    "pnl_usdt",
    "pnl",
    "profit_abs",
    "return_pct",
    "normalized_return_pct",
    "net_return_pct",
    "realized_pnl",
)
TRADE_ID_COLUMN_CANDIDATES = ("trade_id", "id", "order_id")
SYMBOL_COLUMN_CANDIDATES = ("symbol", "pair", "moeda")
SIDE_COLUMN_CANDIDATES = ("side", "position_side", "direction", "fechar_side", "is_short")
TIMESTAMP_COLUMN_CANDIDATES = (
    "opened_at",
    "open_time_utc",
    "timestamp",
    "date",
    "open_1m_ts",
    "open_date",
    "close_date",
    "horario_abertura",
    "horario_fechamento",
)
REGIME_COLUMN_CANDIDATES = ("regime", "market_regime", "volatility_regime")
STRATEGY_COLUMN_CANDIDATES = ("strategy", "enter_tag", "model_name", "strategy_name")
EQUITY_COLUMN_CANDIDATES = ("equity", "balance", "account_equity")
BASE_COLUMN_CANDIDATES = ("starting_equity", "initial_balance", "base_capital", "stake_amount")

SAFETY_DEFAULTS = {
    "runtime_mode": "paper",
    "shadow_only": True,
    "live_trading_enabled": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
}


class PaperFinancialPerformanceError(ValueError):
    """Raised only for programmer errors in the paper metrics module."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        value = float(value)
    if isinstance(value, float):
        return value if np.isfinite(value) else None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if value is pd.NA:
        return None
    return value


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(json_safe(payload), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def resolve_column(frame: pd.DataFrame, candidates: tuple[str, ...], explicit: str | None = None) -> str | None:
    if explicit:
        return explicit if explicit in frame.columns else None
    for candidate in candidates:
        if candidate in frame.columns:
            return candidate
    return None


def read_table(path: str | Path) -> pd.DataFrame:
    target = Path(path)
    suffix = target.suffix.lower()

    if suffix == ".parquet":
        return pd.read_parquet(target)
    if suffix == ".csv":
        return pd.read_csv(target)
    if suffix == ".jsonl":
        return pd.read_json(target, lines=True)
    if suffix == ".json":
        return pd.read_json(target)

    raise ValueError(f"unsupported_source_format:{target}")


def empty_metrics() -> dict[str, Any]:
    return {
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": None,
        "total_pnl": 0.0,
        "avg_return": None,
        "median_return": None,
        "expectancy": None,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "profit_factor": None,
        "profit_factor_status": "no_trades",
        "avg_win": None,
        "avg_loss": None,
        "payoff_ratio": None,
        "max_drawdown": None,
        "max_drawdown_pct": None,
        "consecutive_wins": 0,
        "consecutive_losses": 0,
    }


def max_consecutive(mask: np.ndarray) -> int:
    best = 0
    current = 0
    for value in mask:
        if bool(value):
            current += 1
            best = max(best, current)
        else:
            current = 0
    return int(best)


def _safe_numeric_pnl(frame: pd.DataFrame) -> pd.Series:
    if "__pnl" not in frame.columns:
        return pd.Series(dtype="float64")
    return pd.to_numeric(frame["__pnl"], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()


def compute_financial_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    pnl = _safe_numeric_pnl(frame)
    if pnl.empty:
        return empty_metrics()

    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    gross_profit = float(wins.sum()) if not wins.empty else 0.0
    gross_loss = float(abs(losses.sum())) if not losses.empty else 0.0

    if gross_loss > 0:
        profit_factor: float | None = gross_profit / gross_loss
        profit_factor_status = "ok"
    elif gross_profit > 0:
        profit_factor = None
        profit_factor_status = "no_losses"
    else:
        profit_factor = None
        profit_factor_status = "no_gains_or_losses"

    avg_win = float(wins.mean()) if not wins.empty else None
    avg_loss = float(losses.mean()) if not losses.empty else None
    payoff_ratio = None
    if avg_win is not None and avg_loss not in (None, 0):
        payoff_ratio = avg_win / abs(avg_loss)

    equity_curve = pnl.cumsum()
    running_peak = equity_curve.cummax()
    drawdown = running_peak - equity_curve
    max_drawdown = float(drawdown.max()) if not drawdown.empty else None

    max_drawdown_pct = None
    if "__base_equity" in frame.columns:
        base = pd.to_numeric(frame["__base_equity"], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
        if not base.empty and float(base.iloc[0]) != 0 and max_drawdown is not None:
            max_drawdown_pct = float(max_drawdown / abs(float(base.iloc[0])))

    return {
        "trades": int(len(pnl)),
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "win_rate": float(len(wins) / len(pnl)),
        "total_pnl": float(pnl.sum()),
        "avg_return": float(pnl.mean()),
        "median_return": float(pnl.median()),
        "expectancy": float(pnl.mean()),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "profit_factor_status": profit_factor_status,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": payoff_ratio,
        "max_drawdown": max_drawdown,
        "max_drawdown_pct": max_drawdown_pct,
        "consecutive_wins": max_consecutive((pnl > 0).to_numpy()),
        "consecutive_losses": max_consecutive((pnl < 0).to_numpy()),
    }


def summarize_by_column(frame: pd.DataFrame, column: str | None) -> list[dict[str, Any]]:
    if not column or column not in frame.columns:
        return []

    rows: list[dict[str, Any]] = []
    for value, group in frame.groupby(column, dropna=False, sort=True):
        label = "UNKNOWN" if pd.isna(value) else str(value)
        rows.append({"value": label, **compute_financial_metrics(group)})
    return rows


def summarize_by_period(frame: pd.DataFrame, timestamp_column: str | None, period: str) -> list[dict[str, Any]]:
    if not timestamp_column or timestamp_column not in frame.columns:
        return []

    tmp = frame.copy()
    ts = pd.to_datetime(tmp[timestamp_column], errors="coerce", utc=True)
    tmp = tmp.loc[ts.notna()].copy()
    if tmp.empty:
        return []

    ts = ts.loc[tmp.index]
    if period == "month":
        tmp["__period"] = ts.dt.strftime("%Y-%m")
    elif period == "day":
        tmp["__period"] = ts.dt.strftime("%Y-%m-%d")
    else:
        raise PaperFinancialPerformanceError(f"unsupported_period:{period}")

    rows: list[dict[str, Any]] = []
    for value, group in tmp.groupby("__period", sort=True):
        rows.append({"period": str(value), **compute_financial_metrics(group)})
    return rows


def blocked_payload(
    status: str,
    reason: str,
    *,
    source_path: str | Path | None,
    report_path: str | Path,
    rows: int = 0,
    minimum_recommended_trades: int | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "source_path": str(source_path) if source_path is not None else None,
        "report_path": str(report_path),
        "rows": int(rows),
        "pnl_column": None,
        "timestamp_column": None,
        "symbol_column": None,
        "side_column": None,
        "regime_column": None,
        "strategy_column": None,
        "global_metrics": empty_metrics(),
        "symbol_summary": [],
        "side_summary": [],
        "regime_summary": [],
        "strategy_summary": [],
        "monthly_summary": [],
        "daily_summary": [],
        "sample_size": int(rows),
        "sample_warning": None,
        "minimum_recommended_trades": minimum_recommended_trades,
        "metrics_reliable": False,
        "generated_at": utc_now(),
        **SAFETY_DEFAULTS,
    }
    if extra:
        payload.update(extra)
    return json_safe(payload)


def run_paper_financial_performance_metrics(
    trades: pd.DataFrame,
    *,
    source_path: str | Path,
    report_path: str | Path = DEFAULT_REPORT_PATH,
    pnl_column: str | None = None,
    timestamp_column: str | None = None,
    symbol_column: str | None = None,
    side_column: str | None = None,
    regime_column: str | None = None,
    strategy_column: str | None = None,
    minimum_recommended_trades: int = 30,
    require_timestamp: bool = False,
) -> dict[str, Any]:
    if not isinstance(trades, pd.DataFrame):
        payload = blocked_payload(
            INVALID_SCHEMA,
            "source_must_be_dataframe",
            source_path=source_path,
            report_path=report_path,
            minimum_recommended_trades=minimum_recommended_trades,
        )
        write_json(report_path, payload)
        return payload

    if trades.empty:
        payload = blocked_payload(
            MISSING_SOURCE,
            "source_empty",
            source_path=source_path,
            report_path=report_path,
            minimum_recommended_trades=minimum_recommended_trades,
        )
        write_json(report_path, payload)
        return payload

    resolved_pnl_column = resolve_column(trades, PNL_COLUMN_CANDIDATES, pnl_column)
    if not resolved_pnl_column:
        payload = blocked_payload(
            MISSING_PNL_COLUMN,
            "missing_pnl_column",
            source_path=source_path,
            report_path=report_path,
            rows=len(trades),
            minimum_recommended_trades=minimum_recommended_trades,
        )
        write_json(report_path, payload)
        return payload

    prepared = trades.copy()
    prepared["__pnl"] = pd.to_numeric(prepared[resolved_pnl_column], errors="coerce").replace([np.inf, -np.inf], np.nan)
    if prepared["__pnl"].isna().any():
        payload = blocked_payload(
            INVALID_SCHEMA,
            "pnl_column_contains_null_or_non_finite_values",
            source_path=source_path,
            report_path=report_path,
            rows=len(trades),
            minimum_recommended_trades=minimum_recommended_trades,
            extra={"pnl_column": resolved_pnl_column},
        )
        write_json(report_path, payload)
        return payload

    resolved_timestamp_column = resolve_column(prepared, TIMESTAMP_COLUMN_CANDIDATES, timestamp_column)
    if require_timestamp and not resolved_timestamp_column:
        payload = blocked_payload(
            MISSING_TIMESTAMP_COLUMN,
            "missing_timestamp_column",
            source_path=source_path,
            report_path=report_path,
            rows=len(trades),
            minimum_recommended_trades=minimum_recommended_trades,
            extra={"pnl_column": resolved_pnl_column},
        )
        write_json(report_path, payload)
        return payload

    if require_timestamp and resolved_timestamp_column:
        ts = pd.to_datetime(prepared[resolved_timestamp_column], errors="coerce", utc=True)
        if ts.isna().any():
            payload = blocked_payload(
                INVALID_SCHEMA,
                "timestamp_column_contains_null_or_invalid_values",
                source_path=source_path,
                report_path=report_path,
                rows=len(trades),
                minimum_recommended_trades=minimum_recommended_trades,
                extra={"pnl_column": resolved_pnl_column, "timestamp_column": resolved_timestamp_column},
            )
            write_json(report_path, payload)
            return payload

    resolved_symbol_column = resolve_column(prepared, SYMBOL_COLUMN_CANDIDATES, symbol_column)
    resolved_side_column = resolve_column(prepared, SIDE_COLUMN_CANDIDATES, side_column)
    resolved_regime_column = resolve_column(prepared, REGIME_COLUMN_CANDIDATES, regime_column)
    resolved_strategy_column = resolve_column(prepared, STRATEGY_COLUMN_CANDIDATES, strategy_column)
    resolved_equity_column = resolve_column(prepared, EQUITY_COLUMN_CANDIDATES)
    resolved_base_column = resolve_column(prepared, BASE_COLUMN_CANDIDATES)

    if resolved_base_column:
        prepared["__base_equity"] = pd.to_numeric(prepared[resolved_base_column], errors="coerce")
    elif resolved_equity_column:
        equity = pd.to_numeric(prepared[resolved_equity_column], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
        if not equity.empty:
            prepared["__base_equity"] = float(equity.iloc[0])

    sample_size = int(len(prepared))
    minimum = int(minimum_recommended_trades)
    sample_warning = None
    metrics_reliable = True
    if sample_size < minimum:
        sample_warning = f"sample_below_minimum_recommended_trades:{sample_size}:{minimum}"
        metrics_reliable = False

    payload = {
        "status": OK,
        "reason": None,
        "source_path": str(source_path),
        "report_path": str(report_path),
        "rows": sample_size,
        "pnl_column": resolved_pnl_column,
        "timestamp_column": resolved_timestamp_column,
        "symbol_column": resolved_symbol_column,
        "side_column": resolved_side_column,
        "regime_column": resolved_regime_column,
        "strategy_column": resolved_strategy_column,
        "global_metrics": compute_financial_metrics(prepared),
        "symbol_summary": summarize_by_column(prepared, resolved_symbol_column),
        "side_summary": summarize_by_column(prepared, resolved_side_column),
        "regime_summary": summarize_by_column(prepared, resolved_regime_column),
        "strategy_summary": summarize_by_column(prepared, resolved_strategy_column),
        "monthly_summary": summarize_by_period(prepared, resolved_timestamp_column, "month"),
        "daily_summary": summarize_by_period(prepared, resolved_timestamp_column, "day"),
        "sample_size": sample_size,
        "sample_warning": sample_warning,
        "minimum_recommended_trades": minimum,
        "metrics_reliable": metrics_reliable,
        "generated_at": utc_now(),
        **SAFETY_DEFAULTS,
    }
    payload = json_safe(payload)
    write_json(report_path, payload)
    return payload


def discover_source(candidates: list[str | Path] | tuple[str | Path, ...] = DEFAULT_SOURCE_CANDIDATES) -> Path | None:
    for candidate in candidates:
        target = Path(candidate)
        if target.exists():
            return target
    return None


def run_paper_financial_performance_metrics_from_paths(
    *,
    source_path: str | Path | None = None,
    source_candidates: list[str | Path] | tuple[str | Path, ...] = DEFAULT_SOURCE_CANDIDATES,
    report_path: str | Path = DEFAULT_REPORT_PATH,
    pnl_column: str | None = None,
    timestamp_column: str | None = None,
    symbol_column: str | None = None,
    side_column: str | None = None,
    regime_column: str | None = None,
    strategy_column: str | None = None,
    minimum_recommended_trades: int = 30,
    require_timestamp: bool = False,
) -> dict[str, Any]:
    resolved_source = Path(source_path) if source_path else discover_source(source_candidates)
    if resolved_source is None or not resolved_source.exists():
        payload = blocked_payload(
            MISSING_SOURCE,
            "missing_source",
            source_path=source_path,
            report_path=report_path,
            minimum_recommended_trades=minimum_recommended_trades,
        )
        write_json(report_path, payload)
        return payload

    try:
        trades = read_table(resolved_source)
    except Exception as exc:
        payload = blocked_payload(
            INVALID_SCHEMA,
            f"read_failed:{exc}",
            source_path=resolved_source,
            report_path=report_path,
            minimum_recommended_trades=minimum_recommended_trades,
        )
        write_json(report_path, payload)
        return payload

    return run_paper_financial_performance_metrics(
        trades,
        source_path=resolved_source,
        report_path=report_path,
        pnl_column=pnl_column,
        timestamp_column=timestamp_column,
        symbol_column=symbol_column,
        side_column=side_column,
        regime_column=regime_column,
        strategy_column=strategy_column,
        minimum_recommended_trades=minimum_recommended_trades,
        require_timestamp=require_timestamp,
    )
