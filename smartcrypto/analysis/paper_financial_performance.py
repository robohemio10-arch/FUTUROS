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
STRATEGY_COLUMN_CANDIDATES = ("strategy", "enter_tag", "model_name")
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
    pass


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
        return blocked_payload(
            INVALID_SCHEMA,
            "source_must_be_dataframe",
            source_path=source_path,
            report_path=report_path,
            minimum_recommended_trades=minimum_recommended_trades,
        )
    if trades.empty:
        return blocked_payload(
            MISSING_SOURCE,
            "source_empty",
            source_path=source_path,
            report_path=report_path,
            minimum_recommended_trades=minimum_recommended_trades,
        )

    pnl_column = pnl_column or resolve_column(trades, PNL_COLUMN_CANDIDATES)
    if not pnl_column:
        return blocked_payload(
            MISSING_PNL_COLUMN,
            "missing_pnl_column",
            source_path=source_path,
            report_path=report_path,
            rows=len(trades),
            minimum_recommended_trades=minimum_recommended_trades,
        )

    timestamp_column = timestamp_column or resolve_column(trades, TIMESTAMP_COLUMN_CANDIDATES)
    if require_timestamp and not timestamp_column:
        return blocked_payload(
            MISSING_TIMESTAMP_COLUMN,
            "missing_timestamp_column",
            source_path=source_path,
            report_path=report_path,
            rows=len(trades),
            pnl_column=pnl_column,
            minimum_recommended_trades=minimum_recommended_trades,
        )

    symbol_column = symbol_column or resolve_column(trades, SYMBOL_COLUMN_CANDIDATES)
    side_column = side_column or resolve_column(trades, SIDE_COLUMN_CANDIDATES)
    regime_column = regime_column or resolve_column(trades, REGIME_COLUMN_CANDIDATES)
    strategy_column = strategy_column or resolve_column(trades, STRATEGY_COLUMN_CANDIDATES)
    equity_column = resolve_column(trades, EQUITY_COLUMN_CANDIDATES)
    base_column = resolve_column(trades, BASE_COLUMN_CANDIDATES)

    normalized = normalize_trades(
        trades,
        pnl_column=pnl_column,
        timestamp_column=timestamp_column,
        symbol_column=symbol_column,
        side_column=side_column,
        regime_column=regime_column,
        strategy_column=strategy_column,
    )
    invalid_reason = invalid_schema_reason(normalized)
    if invalid_reason:
        return blocked_payload(
            INVALID_SCHEMA,
            invalid_reason,
            source_path=source_path,
            report_path=report_path,
            rows=len(trades),
            pnl_column=pnl_column,
            timestamp_column=timestamp_column,
            symbol_column=symbol_column,
            side_column=side_column,
            minimum_recommended_trades=minimum_recommended_trades,
        )

    sample_size = int(len(normalized))
    sample_warning = (
        f"sample_below_minimum_recommended_trades:{sample_size}:{int(minimum_recommended_trades)}"
        if sample_size < int(minimum_recommended_trades)
        else None
    )
    metrics_reliable = sample_warning is None
    global_metrics = compute_financial_metrics(
        normalized,
        equity_column=equity_column,
        base_column=base_column,
    )
    payload = {
        "status": OK,
        "reason": None,
        "source_path": str(source_path),
        "report_path": str(report_path),
        "rows": int(len(trades)),
        "pnl_column": pnl_column,
        "timestamp_column": timestamp_column,
        "symbol_column": symbol_column,
        "side_column": side_column,
        "regime_column": regime_column,
        "strategy_column": strategy_column,
        "global_metrics": global_metrics,
        "symbol_summary": summarize_by_column(normalized, "__symbol") if symbol_column else [],
        "side_summary": summarize_by_column(normalized, "__side") if side_column else [],
        "regime_summary": summarize_by_column(normalized, "__regime") if regime_column else [],
        "strategy_summary": summarize_by_column(normalized, "__strategy") if strategy_column else [],
        "monthly_summary": summarize_by_period(normalized, "M") if timestamp_column else [],
        "daily_summary": summarize_by_period(normalized, "D") if timestamp_column else [],
        "sample_size": sample_size,
        "sample_warning": sample_warning,
        "minimum_recommended_trades": int(minimum_recommended_trades),
        "metrics_reliable": bool(metrics_reliable),
        "generated_at": utc_now(),
        **SAFETY_DEFAULTS,
    }
    return json_safe(payload)


def run_paper_financial_performance_metrics_from_paths(
    *,
    source_path: str | Path | None = None,
    source_candidates: list[str | Path] | tuple[str | Path, ...] = DEFAULT_SOURCE_CANDIDATES,
    report_path: str | Path = DEFAULT_REPORT_PATH,
    **kwargs: Any,
) -> dict[str, Any]:
    resolved_source = resolve_source_path(source_path, source_candidates)
    if resolved_source is None:
        payload = blocked_payload(
            MISSING_SOURCE,
            "missing_source",
            source_path=source_path or first_candidate_label(source_candidates),
            report_path=report_path,
            minimum_recommended_trades=kwargs.get("minimum_recommended_trades", 30),
        )
        write_json(report_path, payload)
        return payload

    try:
        trades = read_local_table(resolved_source)
    except Exception as exc:
        payload = blocked_payload(
            INVALID_SCHEMA,
            f"source_read_failed:{exc}",
            source_path=resolved_source,
            report_path=report_path,
            minimum_recommended_trades=kwargs.get("minimum_recommended_trades", 30),
        )
        write_json(report_path, payload)
        return payload

    payload = run_paper_financial_performance_metrics(
        trades,
        source_path=resolved_source,
        report_path=report_path,
        **kwargs,
    )
    write_json(report_path, payload)
    return payload


def normalize_trades(
    trades: pd.DataFrame,
    *,
    pnl_column: str,
    timestamp_column: str | None,
    symbol_column: str | None,
    side_column: str | None,
    regime_column: str | None,
    strategy_column: str | None,
) -> pd.DataFrame:
    normalized = trades.copy()
    normalized["__pnl"] = pd.to_numeric(normalized[pnl_column], errors="coerce")
    if timestamp_column:
        normalized["__timestamp"] = pd.to_datetime(normalized[timestamp_column], errors="coerce", utc=True)
        normalized = normalized.sort_values("__timestamp", kind="stable").reset_index(drop=True)
    else:
        normalized = normalized.reset_index(drop=True)
    if symbol_column:
        normalized["__symbol"] = normalized[symbol_column].map(clean_label)
    if side_column:
        normalized["__side"] = normalize_side(normalized[side_column])
    if regime_column:
        normalized["__regime"] = normalized[regime_column].map(clean_label)
    if strategy_column:
        normalized["__strategy"] = normalized[strategy_column].map(clean_label)
    return normalized


def invalid_schema_reason(frame: pd.DataFrame) -> str | None:
    pnl = frame["__pnl"].replace([np.inf, -np.inf], np.nan)
    if pnl.isna().any():
        return "pnl_column_contains_null_or_non_finite_values"
    if "__timestamp" in frame.columns and frame["__timestamp"].isna().any():
        return "timestamp_column_contains_null_or_unparseable_values"
    return None


def compute_financial_metrics(
    frame: pd.DataFrame,
    *,
    equity_column: str | None = None,
    base_column: str | None = None,
) -> dict[str, Any]:
    if frame.empty:
        return empty_metrics()
    pnl = pd.to_numeric(frame["__pnl"], errors="coerce").to_numpy(dtype=float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    gross_profit = float(wins.sum()) if wins.size else 0.0
    gross_loss = float(abs(losses.sum())) if losses.size else 0.0
    avg_win = float(wins.mean()) if wins.size else None
    avg_loss = float(losses.mean()) if losses.size else None
    profit_factor, profit_factor_status = controlled_profit_factor(gross_profit, gross_loss)
    drawdown = max_drawdown(pnl)
    return {
        "trades": int(len(pnl)),
        "wins": int((pnl > 0).sum()),
        "losses": int((pnl < 0).sum()),
        "win_rate": float((pnl > 0).mean()) if pnl.size else None,
        "total_pnl": float(pnl.sum()) if pnl.size else 0.0,
        "total_return": float(pnl.sum()) if pnl.size else 0.0,
        "avg_return": float(pnl.mean()) if pnl.size else None,
        "median_return": float(np.median(pnl)) if pnl.size else None,
        "expectancy": float(pnl.mean()) if pnl.size else None,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "profit_factor_status": profit_factor_status,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": float(avg_win / abs(avg_loss)) if avg_win is not None and avg_loss not in (None, 0) else None,
        "max_drawdown": drawdown,
        "max_drawdown_pct": max_drawdown_pct(frame, drawdown, equity_column=equity_column, base_column=base_column),
        "consecutive_wins": max_consecutive(pnl > 0),
        "consecutive_losses": max_consecutive(pnl < 0),
    }


def empty_metrics() -> dict[str, Any]:
    return {
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": None,
        "total_pnl": 0.0,
        "total_return": 0.0,
        "avg_return": None,
        "median_return": None,
        "expectancy": None,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "profit_factor": None,
        "profit_factor_status": "no_rows",
        "avg_win": None,
        "avg_loss": None,
        "payoff_ratio": None,
        "max_drawdown": None,
        "max_drawdown_pct": None,
        "consecutive_wins": 0,
        "consecutive_losses": 0,
    }


def controlled_profit_factor(gross_profit: float, gross_loss: float) -> tuple[float | None, str]:
    if gross_loss > 0:
        return float(gross_profit / gross_loss), "ok"
    if gross_profit > 0:
        return None, "no_losses"
    return None, "no_gains_or_losses"


def max_drawdown(pnl: np.ndarray) -> float:
    if pnl.size == 0:
        return 0.0
    curve = np.cumsum(pnl)
    peaks = np.maximum.accumulate(np.insert(curve, 0, 0.0))[1:]
    drawdowns = peaks - curve
    return float(drawdowns.max()) if drawdowns.size else 0.0


def max_drawdown_pct(
    frame: pd.DataFrame,
    drawdown: float,
    *,
    equity_column: str | None,
    base_column: str | None,
) -> float | None:
    if equity_column and equity_column in frame.columns:
        equity = pd.to_numeric(frame[equity_column], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
        if not equity.empty and float(equity.max()) > 0:
            return float(drawdown / float(equity.max()))
    if base_column and base_column in frame.columns:
        base = pd.to_numeric(frame[base_column], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
        if not base.empty and float(base.iloc[0]) > 0:
            return float(drawdown / float(base.iloc[0]))
    return None


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


def summarize_by_column(frame: pd.DataFrame, column: str) -> list[dict[str, Any]]:
    if column not in frame.columns:
        return []
    rows: list[dict[str, Any]] = []
    for value, group in frame.groupby(column, dropna=False, sort=True):
        rows.append({"value": clean_label(value), **compute_financial_metrics(group)})
    return rows


def summarize_by_period(frame: pd.DataFrame, frequency: str) -> list[dict[str, Any]]:
    if "__timestamp" not in frame.columns:
        return []
    working = frame.copy()
    if frequency == "M":
        working["__period"] = working["__timestamp"].dt.strftime("%Y-%m")
    else:
        working["__period"] = working["__timestamp"].dt.strftime("%Y-%m-%d")
    return [{"period": row.pop("value"), **row} for row in summarize_by_column(working, "__period")]


def normalize_side(series: pd.Series) -> pd.Series:
    def normalize(value: Any) -> str:
        if pd.isna(value):
            return "UNKNOWN"
        text = str(value).strip().upper()
        if text in {"1", "TRUE", "SHORT", "SELL"}:
            return "SHORT"
        if text in {"0", "FALSE", "LONG", "BUY"}:
            return "LONG"
        return text

    return series.map(normalize)


def read_local_table(path: str | Path) -> pd.DataFrame:
    target = Path(path)
    suffix = target.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(target)
    if suffix == ".csv":
        return pd.read_csv(target)
    if suffix == ".jsonl":
        return pd.read_json(target, lines=True)
    if suffix == ".json":
        payload = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return pd.DataFrame(payload)
        if isinstance(payload, dict):
            for key in ("trades", "rows", "data"):
                if isinstance(payload.get(key), list):
                    return pd.DataFrame(payload[key])
        raise PaperFinancialPerformanceError("json_source_does_not_contain_trade_rows")
    raise PaperFinancialPerformanceError(f"unsupported_source_extension:{suffix}")


def resolve_source_path(
    source_path: str | Path | None,
    source_candidates: list[str | Path] | tuple[str | Path, ...],
) -> Path | None:
    if source_path:
        target = Path(source_path)
        return target if target.exists() else None
    for candidate in source_candidates:
        target = Path(candidate)
        if target.exists():
            return target
    return None


def first_candidate_label(source_candidates: list[str | Path] | tuple[str | Path, ...]) -> str:
    if not source_candidates:
        return ""
    return str(source_candidates[0])


def blocked_payload(
    status: str,
    reason: str,
    *,
    source_path: str | Path | None,
    report_path: str | Path,
    rows: int = 0,
    pnl_column: str | None = None,
    timestamp_column: str | None = None,
    symbol_column: str | None = None,
    side_column: str | None = None,
    minimum_recommended_trades: int = 30,
) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "source_path": str(source_path) if source_path is not None else None,
        "report_path": str(report_path),
        "rows": int(rows),
        "pnl_column": pnl_column,
        "timestamp_column": timestamp_column,
        "symbol_column": symbol_column,
        "side_column": side_column,
        "global_metrics": empty_metrics(),
        "symbol_summary": [],
        "side_summary": [],
        "regime_summary": [],
        "strategy_summary": [],
        "monthly_summary": [],
        "daily_summary": [],
        "sample_size": 0,
        "sample_warning": reason,
        "minimum_recommended_trades": int(minimum_recommended_trades),
        "metrics_reliable": False,
        "generated_at": utc_now(),
        **SAFETY_DEFAULTS,
    }


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(json_safe(payload), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def resolve_column(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if candidate in frame.columns:
            return candidate
    return None


def clean_label(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    return str(value)


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float):
        return value if np.isfinite(value) else None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
