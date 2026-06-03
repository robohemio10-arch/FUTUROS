from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


DEFAULT_REPORT_PATH = Path("data/reports/event_driven_backtest_report.json")
DEFAULT_TIMESTAMP_COLUMN = "timestamp"
DEFAULT_SYMBOL_COLUMN = "symbol"
DEFAULT_SIDE_COLUMN = "side"
DEFAULT_PRICE_COLUMN = "close"
SIDE_LONG = "long"
SIDE_SHORT = "short"


class EventDrivenBacktestError(ValueError):
    pass


def run_event_driven_backtest(
    *,
    signals_path: str | Path,
    candles_path: str | Path,
    report_path: str | Path | None = DEFAULT_REPORT_PATH,
    timestamp_column: str = DEFAULT_TIMESTAMP_COLUMN,
    symbol_column: str = DEFAULT_SYMBOL_COLUMN,
    side_column: str = DEFAULT_SIDE_COLUMN,
    price_column: str = DEFAULT_PRICE_COLUMN,
    fee_bps: float = 0.0,
    spread_bps: float = 0.0,
    slippage_bps: float = 0.0,
    latency_seconds: float = 0.0,
    liquidity_cap: float = 1_000_000.0,
    partial_fill_ratio: float = 1.0,
    seed: int = 42,
    strict: bool = False,
    safety_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    signals_file = Path(signals_path)
    candles_file = Path(candles_path)
    report_file = Path(report_path) if report_path is not None else None
    params = simulation_parameters(
        timestamp_column=timestamp_column,
        symbol_column=symbol_column,
        side_column=side_column,
        price_column=price_column,
        fee_bps=fee_bps,
        spread_bps=spread_bps,
        slippage_bps=slippage_bps,
        latency_seconds=latency_seconds,
        liquidity_cap=liquidity_cap,
        partial_fill_ratio=partial_fill_ratio,
        seed=seed,
    )
    if not signals_file.exists():
        report = blocked_report(
            reason="missing_signals",
            signals_path=signals_file,
            candles_path=candles_file,
            report_path=report_file,
            parameters=params,
        )
        write_report(report, report_file)
        return report
    if not candles_file.exists():
        report = blocked_report(
            reason="missing_candles",
            signals_path=signals_file,
            candles_path=candles_file,
            report_path=report_file,
            parameters=params,
        )
        write_report(report, report_file)
        return report
    signals = read_table(signals_file)
    candles = read_table(candles_file)
    return run_event_driven_backtest_frame(
        signals=signals,
        candles=candles,
        signals_path=signals_file,
        candles_path=candles_file,
        report_path=report_file,
        timestamp_column=timestamp_column,
        symbol_column=symbol_column,
        side_column=side_column,
        price_column=price_column,
        fee_bps=fee_bps,
        spread_bps=spread_bps,
        slippage_bps=slippage_bps,
        latency_seconds=latency_seconds,
        liquidity_cap=liquidity_cap,
        partial_fill_ratio=partial_fill_ratio,
        seed=seed,
        strict=strict,
        safety_overrides=safety_overrides,
    )


def run_event_driven_backtest_frame(
    *,
    signals: pd.DataFrame,
    candles: pd.DataFrame,
    signals_path: str | Path | None = None,
    candles_path: str | Path | None = None,
    report_path: str | Path | None = DEFAULT_REPORT_PATH,
    timestamp_column: str = DEFAULT_TIMESTAMP_COLUMN,
    symbol_column: str = DEFAULT_SYMBOL_COLUMN,
    side_column: str = DEFAULT_SIDE_COLUMN,
    price_column: str = DEFAULT_PRICE_COLUMN,
    fee_bps: float = 0.0,
    spread_bps: float = 0.0,
    slippage_bps: float = 0.0,
    latency_seconds: float = 0.0,
    liquidity_cap: float = 1_000_000.0,
    partial_fill_ratio: float = 1.0,
    seed: int = 42,
    strict: bool = False,
    safety_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report_file = Path(report_path) if report_path is not None else None
    params = simulation_parameters(
        timestamp_column=timestamp_column,
        symbol_column=symbol_column,
        side_column=side_column,
        price_column=price_column,
        fee_bps=fee_bps,
        spread_bps=spread_bps,
        slippage_bps=slippage_bps,
        latency_seconds=latency_seconds,
        liquidity_cap=liquidity_cap,
        partial_fill_ratio=partial_fill_ratio,
        seed=seed,
    )
    safe = safety_payload(safety_overrides)
    safety_errors = unsafe_safety_flags(safe)
    if strict and safety_errors:
        report = blocked_report(
            reason="unsafe_safety_flags",
            signals_path=signals_path,
            candles_path=candles_path,
            report_path=report_file,
            parameters=params,
            safety=safe,
        )
        report["blocking_errors"] = safety_errors
        write_report(report, report_file)
        return report

    validation = validate_inputs(
        signals=signals,
        candles=candles,
        timestamp_column=timestamp_column,
        symbol_column=symbol_column,
        side_column=side_column,
        price_column=price_column,
    )
    if validation["blocking_errors"]:
        report = blocked_report(
            reason=";".join(validation["blocking_errors"]),
            signals_path=signals_path,
            candles_path=candles_path,
            report_path=report_file,
            parameters=params,
            safety=safe,
        )
        report.update(validation)
        write_report(report, report_file)
        return report

    rng = np.random.default_rng(int(seed))
    normalized_signals = normalize_signals(signals, timestamp_column, symbol_column, side_column)
    normalized_candles = normalize_candles(candles, timestamp_column, symbol_column, price_column)
    events = simulate_events(
        signals=normalized_signals,
        candles=normalized_candles,
        price_column=price_column,
        fee_bps=float(fee_bps),
        spread_bps=float(spread_bps),
        slippage_bps=float(slippage_bps),
        latency_seconds=float(latency_seconds),
        liquidity_cap=float(liquidity_cap),
        partial_fill_ratio=float(partial_fill_ratio),
        rng=rng,
    )
    report = build_report(
        events=events,
        signals_path=signals_path,
        candles_path=candles_path,
        report_path=report_file,
        input_rows=len(signals),
        candle_rows=len(candles),
        parameters=params,
        safety=safe,
    )
    write_report(report, report_file)
    return report


def validate_inputs(
    *,
    signals: pd.DataFrame,
    candles: pd.DataFrame,
    timestamp_column: str,
    symbol_column: str,
    side_column: str,
    price_column: str,
) -> dict[str, Any]:
    blocking: list[str] = []
    if not isinstance(signals, pd.DataFrame) or signals.empty:
        blocking.append("missing_or_empty_signals")
    if not isinstance(candles, pd.DataFrame) or candles.empty:
        blocking.append("missing_or_empty_candles")
    signal_missing = [
        column for column in (timestamp_column, symbol_column, side_column) if column not in getattr(signals, "columns", [])
    ]
    candle_missing = [
        column for column in (timestamp_column, symbol_column, price_column) if column not in getattr(candles, "columns", [])
    ]
    if signal_missing or candle_missing:
        blocking.append(f"missing_timestamp_or_required_columns:signals={signal_missing}:candles={candle_missing}")
    if blocking:
        return {"blocking_errors": blocking}

    normalized_candles = normalize_candles(candles, timestamp_column, symbol_column, price_column)
    if normalized_candles["_event_time"].isna().any():
        blocking.append("candle_timestamps_unparseable")
    if normalize_signals(signals, timestamp_column, symbol_column, side_column)["_decision_time"].isna().any():
        blocking.append("signal_timestamps_unparseable")
    sorted_check = normalized_candles.sort_values([symbol_column, "_event_time"], kind="stable")
    if not normalized_candles.reset_index(drop=True).equals(sorted_check.reset_index(drop=True)):
        blocking.append("candles_out_of_order")
    duplicates = normalized_candles.duplicated(subset=[symbol_column, "_event_time"]).any()
    if bool(duplicates):
        blocking.append("duplicate_candles_without_policy")
    return {"blocking_errors": blocking}


def simulate_events(
    *,
    signals: pd.DataFrame,
    candles: pd.DataFrame,
    price_column: str,
    fee_bps: float,
    spread_bps: float,
    slippage_bps: float,
    latency_seconds: float,
    liquidity_cap: float,
    partial_fill_ratio: float,
    rng: np.random.Generator,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for _, signal in signals.sort_values("_decision_time", kind="stable").iterrows():
        symbol = signal["_symbol"]
        side = signal["_side"]
        decision_time = signal["_decision_time"]
        stake = float(signal.get("stake", signal.get("notional", 100.0)))
        requested_notional = max(stake, 0.0)
        event = {
            "decision_time": to_iso(decision_time),
            "symbol": symbol,
            "side": side,
            "requested_notional": requested_notional,
            "status": "skipped",
            "skip_reason": None,
            "entry_time": None,
            "exit_time": None,
            "entry_price": None,
            "exit_price": None,
            "fill_ratio": 0.0,
            "gross_pnl": 0.0,
            "net_pnl": 0.0,
            "fees": 0.0,
            "spread_cost": 0.0,
            "slippage_cost": 0.0,
            "api_reject_simulated": False,
            "timeout_simulated": False,
        }
        if requested_notional <= 0:
            event["skip_reason"] = "invalid_notional"
            events.append(event)
            continue
        eligible_time = decision_time + pd.Timedelta(seconds=float(latency_seconds))
        symbol_candles = candles.loc[candles["_symbol"].eq(symbol)].copy()
        entry = next_candle(symbol_candles, eligible_time)
        if entry is None:
            event["status"] = "no_fill"
            event["skip_reason"] = "no_future_candle"
            events.append(event)
            continue
        if entry["_event_time"] < decision_time:
            event["status"] = "rejected"
            event["skip_reason"] = "execution_before_decision_timestamp"
            events.append(event)
            continue
        exit_candle = next_candle(symbol_candles, entry["_event_time"] + pd.Timedelta(microseconds=1))
        if exit_candle is None:
            event["status"] = "no_fill"
            event["skip_reason"] = "no_future_exit_candle"
            events.append(event)
            continue

        deterministic_reject = bool(signal.get("simulate_reject", False))
        deterministic_timeout = bool(signal.get("simulate_timeout", False))
        # Keep seeded hooks available without making ordinary fixtures flaky.
        random_reject = bool(signal.get("reject_probability", 0.0)) and rng.random() < float(signal.get("reject_probability", 0.0))
        if deterministic_reject or random_reject:
            event["status"] = "rejected"
            event["skip_reason"] = "api_reject_simulated"
            event["api_reject_simulated"] = True
            events.append(event)
            continue
        if deterministic_timeout:
            event["status"] = "no_fill"
            event["skip_reason"] = "timeout_simulated"
            event["timeout_simulated"] = True
            events.append(event)
            continue

        liquidity_notional = min(requested_notional, max(float(liquidity_cap), 0.0))
        fill_ratio = min(1.0, max(0.0, float(partial_fill_ratio)))
        executed_notional = liquidity_notional * fill_ratio
        if executed_notional <= 0:
            event["status"] = "no_fill"
            event["skip_reason"] = "liquidity_cap_zero"
            events.append(event)
            continue

        raw_entry = float(entry[price_column])
        raw_exit = float(exit_candle[price_column])
        entry_price = apply_entry_cost(raw_entry, side, spread_bps=spread_bps, slippage_bps=slippage_bps)
        exit_price = apply_exit_cost(raw_exit, side, spread_bps=spread_bps, slippage_bps=slippage_bps)
        direction = 1.0 if side == SIDE_LONG else -1.0
        gross_pnl = executed_notional * ((exit_price - entry_price) / entry_price) * direction
        fees = executed_notional * (float(fee_bps) / 10000.0) * 2.0
        spread_cost = executed_notional * (float(spread_bps) / 10000.0) * 2.0
        slippage_cost = executed_notional * (float(slippage_bps) / 10000.0) * 2.0
        net_pnl = gross_pnl - fees
        status = "partial_fill" if executed_notional < requested_notional else "executed"
        event.update(
            {
                "status": status,
                "skip_reason": None,
                "entry_time": to_iso(entry["_event_time"]),
                "exit_time": to_iso(exit_candle["_event_time"]),
                "entry_price": entry_price,
                "exit_price": exit_price,
                "fill_ratio": executed_notional / requested_notional,
                "executed_notional": executed_notional,
                "gross_pnl": gross_pnl,
                "net_pnl": net_pnl,
                "fees": fees,
                "spread_cost": spread_cost,
                "slippage_cost": slippage_cost,
            }
        )
        events.append(event)
    return events


def build_report(
    *,
    events: list[dict[str, Any]],
    signals_path: str | Path | None,
    candles_path: str | Path | None,
    report_path: Path | None,
    input_rows: int,
    candle_rows: int,
    parameters: dict[str, Any],
    safety: dict[str, Any],
) -> dict[str, Any]:
    executed = [event for event in events if event["status"] in {"executed", "partial_fill"}]
    skipped = [event for event in events if event["status"] == "skipped"]
    rejected = [event for event in events if event["status"] == "rejected"]
    partial = [event for event in events if event["status"] == "partial_fill"]
    no_fills = [event for event in events if event["status"] == "no_fill"]
    net_values = pd.Series([event["net_pnl"] for event in executed], dtype=float)
    gross_values = pd.Series([event["gross_pnl"] for event in executed], dtype=float)
    financial = financial_summary(gross_values, net_values, executed)
    status = "ok" if executed else "insufficient_data" if no_fills or skipped else "warning"
    reason = "ok" if status == "ok" else "no_executed_trades"
    return {
        "status": status,
        "reason": reason,
        "generated_at_utc": utc_timestamp(),
        "signals_path": str(signals_path) if signals_path is not None else None,
        "candles_path": str(candles_path) if candles_path is not None else None,
        "report_path": str(report_path) if report_path is not None else None,
        "input_rows": int(input_rows),
        "candle_rows": int(candle_rows),
        "simulation_parameters": parameters,
        "execution_summary": {
            "total_signals": int(len(events)),
            "executed_trades": int(len(executed)),
            "skipped_trades": int(len(skipped)),
            "rejected_trades": int(len(rejected)),
            "partial_fills": int(len(partial)),
            "no_fills": int(len(no_fills)),
        },
        "financial_summary": financial,
        "drawdown_summary": drawdown_summary(net_values),
        "baseline_summary": {"available": False, "reason": "not_provided"},
        "execution_quality_summary": execution_quality_summary(events),
        "skipped_reasons": skipped_reasons(events),
        "events": events,
        "signal_producer_updated": False,
        "registry_updated": False,
        "model_updated": False,
        "risk_manager_updated": False,
        "freqtrade_db_touched": False,
        **safety,
    }


def financial_summary(gross_values: pd.Series, net_values: pd.Series, executed: list[dict[str, Any]]) -> dict[str, Any]:
    rows = int(len(net_values))
    wins = net_values.loc[net_values > 0]
    losses = net_values.loc[net_values < 0]
    gross_profit = float(wins.sum()) if len(wins) else 0.0
    gross_loss = abs(float(losses.sum())) if len(losses) else 0.0
    profit_factor = None if gross_loss == 0 and gross_profit > 0 else gross_profit / gross_loss if gross_loss else 0.0
    return {
        "gross_pnl": float(gross_values.sum()) if rows else 0.0,
        "net_pnl": float(net_values.sum()) if rows else 0.0,
        "total_fees": float(sum(event.get("fees", 0.0) for event in executed)),
        "total_slippage_cost": float(sum(event.get("slippage_cost", 0.0) for event in executed)),
        "total_spread_cost": float(sum(event.get("spread_cost", 0.0) for event in executed)),
        "win_rate": float(len(wins) / rows) if rows else 0.0,
        "average_win": float(wins.mean()) if len(wins) else 0.0,
        "average_loss": float(losses.mean()) if len(losses) else 0.0,
        "expectancy": float(net_values.mean()) if rows else 0.0,
        "profit_factor": profit_factor,
    }


def drawdown_summary(net_values: pd.Series) -> dict[str, Any]:
    if net_values.empty:
        return {"max_drawdown": 0.0, "equity_curve_summary": {"points": 0, "final_equity": 0.0}}
    equity = net_values.cumsum()
    drawdown = equity - equity.cummax()
    return {
        "max_drawdown": float(abs(drawdown.min())),
        "equity_curve_summary": {
            "points": int(len(equity)),
            "final_equity": float(equity.iloc[-1]),
            "min_equity": float(equity.min()),
            "max_equity": float(equity.max()),
        },
    }


def execution_quality_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    total = max(len(events), 1)
    executed = [event for event in events if event["status"] in {"executed", "partial_fill"}]
    return {
        "fill_rate": float(len(executed) / total),
        "partial_fill_rate": float(sum(1 for event in events if event["status"] == "partial_fill") / total),
        "reject_rate": float(sum(1 for event in events if event["status"] == "rejected") / total),
        "no_fill_rate": float(sum(1 for event in events if event["status"] == "no_fill") / total),
    }


def skipped_reasons(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        reason = event.get("skip_reason")
        if reason:
            counts[str(reason)] = counts.get(str(reason), 0) + 1
    return counts


def normalize_signals(frame: pd.DataFrame, timestamp_column: str, symbol_column: str, side_column: str) -> pd.DataFrame:
    result = frame.copy()
    result["_decision_time"] = pd.to_datetime(result[timestamp_column], utc=True, errors="coerce")
    result["_symbol"] = result[symbol_column].astype(str).str.upper().str.replace("/", "", regex=False)
    result["_side"] = result[side_column].map(normalize_side)
    return result


def normalize_candles(frame: pd.DataFrame, timestamp_column: str, symbol_column: str, price_column: str) -> pd.DataFrame:
    result = frame.copy()
    result["_event_time"] = pd.to_datetime(result[timestamp_column], utc=True, errors="coerce")
    result["_symbol"] = result[symbol_column].astype(str).str.upper().str.replace("/", "", regex=False)
    result[price_column] = pd.to_numeric(result[price_column], errors="coerce")
    return result


def next_candle(candles: pd.DataFrame, earliest_time: pd.Timestamp) -> pd.Series | None:
    eligible = candles.loc[candles["_event_time"] >= earliest_time].sort_values("_event_time", kind="stable")
    if eligible.empty:
        return None
    return eligible.iloc[0]


def apply_entry_cost(price: float, side: str, *, spread_bps: float, slippage_bps: float) -> float:
    adjustment = (float(spread_bps) / 2.0 + float(slippage_bps)) / 10000.0
    return price * (1.0 + adjustment) if side == SIDE_LONG else price * (1.0 - adjustment)


def apply_exit_cost(price: float, side: str, *, spread_bps: float, slippage_bps: float) -> float:
    adjustment = (float(spread_bps) / 2.0 + float(slippage_bps)) / 10000.0
    return price * (1.0 - adjustment) if side == SIDE_LONG else price * (1.0 + adjustment)


def normalize_side(value: Any) -> str:
    text = str(value).strip().lower()
    if text in {"short", "sell", "s"}:
        return SIDE_SHORT
    return SIDE_LONG


def read_table(path: str | Path) -> pd.DataFrame:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(file_path)
    if suffix == ".csv":
        return pd.read_csv(file_path)
    if suffix == ".jsonl":
        rows = []
        with file_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))
        return pd.DataFrame(rows)
    if suffix == ".json":
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return pd.DataFrame(payload)
        if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
            return pd.DataFrame(payload["rows"])
        return pd.DataFrame([payload])
    raise EventDrivenBacktestError(f"unsupported_input_format:{suffix}")


def simulation_parameters(**kwargs: Any) -> dict[str, Any]:
    return {
        "timestamp_column": kwargs["timestamp_column"],
        "symbol_column": kwargs["symbol_column"],
        "side_column": kwargs["side_column"],
        "price_column": kwargs["price_column"],
        "fee_bps": float(kwargs["fee_bps"]),
        "spread_bps": float(kwargs["spread_bps"]),
        "slippage_bps": float(kwargs["slippage_bps"]),
        "latency_seconds": float(kwargs["latency_seconds"]),
        "liquidity_cap": float(kwargs["liquidity_cap"]),
        "partial_fill_ratio": float(kwargs["partial_fill_ratio"]),
        "seed": int(kwargs["seed"]),
    }


def safety_payload(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "paper_only": True,
        "shadow_only": True,
        "runtime_mode": "paper",
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
    }
    if overrides:
        payload.update(overrides)
    return payload


def unsafe_safety_flags(payload: dict[str, Any]) -> list[str]:
    unsafe = []
    if payload.get("paper_only") is not True:
        unsafe.append("paper_only")
    if payload.get("shadow_only") is not True:
        unsafe.append("shadow_only")
    if payload.get("runtime_mode") != "paper":
        unsafe.append("runtime_mode")
    for key in (
        "live_trading_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_risk",
    ):
        if payload.get(key) is True:
            unsafe.append(key)
    return unsafe


def blocked_report(
    *,
    reason: str,
    signals_path: str | Path | None,
    candles_path: str | Path | None,
    report_path: str | Path | None,
    parameters: dict[str, Any],
    safety: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": reason,
        "generated_at_utc": utc_timestamp(),
        "signals_path": str(signals_path) if signals_path is not None else None,
        "candles_path": str(candles_path) if candles_path is not None else None,
        "report_path": str(report_path) if report_path is not None else None,
        "input_rows": 0,
        "candle_rows": 0,
        "simulation_parameters": parameters,
        "execution_summary": {},
        "financial_summary": {},
        "drawdown_summary": {},
        "baseline_summary": {},
        "skipped_reasons": {},
        "signal_producer_updated": False,
        "registry_updated": False,
        "model_updated": False,
        "risk_manager_updated": False,
        "freqtrade_db_touched": False,
        **(safety or safety_payload()),
    }


def write_report(report: dict[str, Any], report_path: Path | None) -> None:
    if report_path is None:
        return
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def to_iso(value: pd.Timestamp) -> str:
    return pd.Timestamp(value).isoformat().replace("+00:00", "Z")


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
