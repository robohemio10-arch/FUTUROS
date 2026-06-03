from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


DEFAULT_REPORT_PATH = Path("data/reports/market_data_health_audit_report.json")
REPORT_VERSION = "1.0"
SUPPORTED_FORMATS = {".parquet", ".csv", ".json", ".jsonl"}
SAFE_FALSE_FLAGS = (
    "live_trading_enabled",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "changes_risk",
)


@dataclass(frozen=True)
class MarketDataHealthLimits:
    max_candle_age_seconds: int = 300
    max_ticker_age_seconds: int = 60
    max_order_book_age_seconds: int = 30
    max_ws_heartbeat_age_seconds: int = 30
    max_spread_bps: float = 25.0
    min_top_depth: float = 10_000.0
    max_slippage_bps: float = 15.0
    max_latency_ms: float = 1_000.0
    max_ws_rest_delta_seconds: int = 10


def run_market_data_health_audit(
    *,
    candles_path: str | Path | None = None,
    ticker_path: str | Path | None = None,
    order_book_path: str | Path | None = None,
    trades_path: str | Path | None = None,
    ws_heartbeat_path: str | Path | None = None,
    rest_snapshot_path: str | Path | None = None,
    report_path: str | Path | None = DEFAULT_REPORT_PATH,
    symbol_column: str = "symbol",
    timestamp_column: str = "timestamp",
    limits: MarketDataHealthLimits | None = None,
    strict: bool = False,
    safety_overrides: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_time = ensure_utc(now or datetime.now(timezone.utc))
    active_limits = limits or MarketDataHealthLimits()
    safety = safety_payload(safety_overrides)
    sources = load_sources(
        {
            "candles": candles_path,
            "ticker": ticker_path,
            "order_book": order_book_path,
            "trades": trades_path,
            "ws_heartbeat": ws_heartbeat_path,
            "rest_snapshot": rest_snapshot_path,
        }
    )
    validation_errors = [f"unsafe_safety_flag:{flag}" for flag in unsafe_safety_flags(safety)]
    warnings: list[str] = []
    if strict and not any(source["exists"] for source in sources.values()):
        validation_errors.append("missing_required_input")
    if strict and not sources["candles"]["exists"]:
        validation_errors.append("missing_required_input:candles")
    for name, source in sources.items():
        if source["status"] == "blocked":
            validation_errors.append(f"source_read_failed:{name}")
        elif not source["exists"]:
            warnings.append(f"missing_optional_source:{name}")

    frames = {name: source["frame"] for name, source in sources.items()}
    symbols = discover_symbols(frames, symbol_column)
    if strict and not symbols:
        validation_errors.append("missing_symbols")
    symbol_results = {
        symbol: evaluate_symbol(
            symbol=symbol,
            frames=frames,
            symbol_column=symbol_column,
            timestamp_column=timestamp_column,
            limits=active_limits,
            now=current_time,
            strict=strict,
        )
        for symbol in symbols
    }
    guard_results = [guard for result in symbol_results.values() for guard in result["guard_results"]]
    blocked_symbols = sorted(symbol for symbol, result in symbol_results.items() if result["status"] == "blocked")
    warning_symbols = sorted(symbol for symbol, result in symbol_results.items() if result["status"] == "warning")
    missing_data = sorted(symbol for symbol, result in symbol_results.items() if result["status"] == "missing_data")
    validation_errors.extend(
        f"{symbol}:{reason}"
        for symbol, result in symbol_results.items()
        for reason in result.get("validation_errors", [])
    )
    warnings.extend(
        f"{symbol}:{warning}"
        for symbol, result in symbol_results.items()
        for warning in result.get("warnings", [])
    )
    status = global_status(
        validation_errors=validation_errors,
        warnings=warnings,
        blocked_symbols=blocked_symbols,
        missing_data=missing_data,
        strict=strict,
    )
    report = {
        "status": status,
        "reason": "ok" if status == "ok" else ";".join(sorted(set(validation_errors or warnings or ["missing_data"]))),
        "generated_at_utc": utc_timestamp(current_time),
        "report_version": REPORT_VERSION,
        "symbols": sorted(symbols),
        "symbol_results": symbol_results,
        "global_summary": global_summary(symbol_results, sources),
        "guard_results": guard_results,
        "blocked_symbols": blocked_symbols,
        "warning_symbols": warning_symbols,
        "missing_data": missing_data,
        "validation_errors": sorted(set(validation_errors)),
        "warnings": sorted(set(warnings)),
        "limits": asdict(active_limits),
        "sources": source_summary(sources),
        **safety,
    }
    write_json_if_requested(report, Path(report_path) if report_path is not None else None)
    return report


def load_sources(paths: dict[str, str | Path | None]) -> dict[str, dict[str, Any]]:
    return {name: load_source(name, path) for name, path in paths.items()}


def load_source(name: str, path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {"name": name, "path": None, "exists": False, "status": "missing", "frame": pd.DataFrame()}
    target = Path(path)
    if not target.exists():
        return {"name": name, "path": str(target), "exists": False, "status": "missing", "frame": pd.DataFrame()}
    try:
        frame = read_table(target)
    except Exception as exc:
        return {
            "name": name,
            "path": str(target),
            "exists": True,
            "status": "blocked",
            "frame": pd.DataFrame(),
            "error": str(exc),
        }
    return {"name": name, "path": str(target), "exists": True, "status": "ok", "frame": frame}


def read_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_FORMATS:
        raise ValueError(f"unsupported_format:{suffix}")
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".jsonl":
        rows = []
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return pd.DataFrame(rows)
    payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    if isinstance(payload, list):
        return pd.DataFrame(payload)
    if isinstance(payload, dict):
        for key in ("rows", "data", "signals"):
            if isinstance(payload.get(key), list):
                return pd.DataFrame(payload[key])
        return pd.DataFrame([payload])
    return pd.DataFrame()


def discover_symbols(frames: dict[str, pd.DataFrame], symbol_column: str) -> set[str]:
    symbols: set[str] = set()
    for frame in frames.values():
        column = first_existing(frame, (symbol_column, "symbol", "pair", "moeda"))
        if column:
            values = frame[column].dropna().astype(str).str.strip()
            symbols.update(value for value in values if value)
    return symbols


def evaluate_symbol(
    *,
    symbol: str,
    frames: dict[str, pd.DataFrame],
    symbol_column: str,
    timestamp_column: str,
    limits: MarketDataHealthLimits,
    now: datetime,
    strict: bool,
) -> dict[str, Any]:
    data = {name: latest_symbol_row(frame, symbol, symbol_column, timestamp_column) for name, frame in frames.items()}
    metrics = calculate_symbol_metrics(data, timestamp_column=timestamp_column, limits=limits, now=now)
    guard_results = [
        data_freshness_guard(symbol, metrics, limits, strict=strict),
        spread_guard(symbol, metrics, limits),
        liquidity_guard(symbol, metrics, limits),
        latency_guard(symbol, metrics, limits),
        ws_rest_divergence_guard(symbol, metrics, limits, strict=strict),
    ]
    if not pd.isna(metrics["last_order_book_age_seconds"]):
        guard_results.append(order_book_guard(symbol, metrics, limits))
    missing_required = [
        guard["guard"]
        for guard in guard_results
        if guard["status"] == "missing_data" and guard["guard"] in {"DataFreshnessGuard", "WsRestDivergenceGuard"}
    ]
    blocked = [guard for guard in guard_results if guard["status"] == "blocked"]
    warnings = [warning for guard in guard_results for warning in guard.get("warnings", [])]
    validation_errors = [reason for guard in blocked for reason in guard.get("reasons", [])]
    if strict and missing_required:
        validation_errors.extend(f"missing_required_guard:{guard}" for guard in missing_required)
    if validation_errors:
        status = "blocked"
    elif any(guard["status"] == "warning" for guard in guard_results):
        status = "warning"
    elif any(guard["status"] == "missing_data" for guard in guard_results):
        status = "missing_data"
    else:
        status = "ok"
    return {
        "symbol": symbol,
        "status": status,
        "metrics": metrics,
        "guard_results": guard_results,
        "validation_errors": sorted(set(validation_errors)),
        "warnings": sorted(set(warnings)),
    }


def latest_symbol_row(
    frame: pd.DataFrame,
    symbol: str,
    symbol_column: str,
    timestamp_column: str,
) -> dict[str, Any] | None:
    if frame.empty:
        return None
    resolved_symbol_column = first_existing(frame, (symbol_column, "symbol", "pair", "moeda"))
    if resolved_symbol_column:
        frame = frame.loc[frame[resolved_symbol_column].astype(str).str.strip() == symbol]
    if frame.empty:
        return None
    resolved_timestamp_column = resolve_timestamp_column(frame, timestamp_column)
    if resolved_timestamp_column:
        parsed = pd.to_datetime(frame[resolved_timestamp_column], utc=True, errors="coerce")
        valid = frame.loc[parsed.notna()].copy()
        if not valid.empty:
            valid["_parsed_timestamp"] = parsed.loc[parsed.notna()]
            return valid.sort_values("_parsed_timestamp").iloc[-1].to_dict()
    return frame.iloc[-1].to_dict()


def calculate_symbol_metrics(
    data: dict[str, dict[str, Any] | None],
    *,
    timestamp_column: str,
    limits: MarketDataHealthLimits,
    now: datetime,
) -> dict[str, Any]:
    candle_age = row_age(data["candles"], timestamp_column, now)
    ticker_age = row_age(data["ticker"], timestamp_column, now, fallbacks=("ticker_timestamp", "ticker_timestamp_utc"))
    order_book_age = row_age(data["order_book"], timestamp_column, now, fallbacks=("order_book_timestamp", "order_book_timestamp_utc"))
    ws_age = row_age(data["ws_heartbeat"], timestamp_column, now, fallbacks=("heartbeat_timestamp", "ws_timestamp", "ws_heartbeat_timestamp"))
    rest_age = row_age(data["rest_snapshot"], timestamp_column, now, fallbacks=("rest_timestamp", "rest_snapshot_timestamp"))
    ws_ts = row_timestamp(data["ws_heartbeat"], timestamp_column, fallbacks=("heartbeat_timestamp", "ws_timestamp", "ws_heartbeat_timestamp"))
    rest_ts = row_timestamp(data["rest_snapshot"], timestamp_column, fallbacks=("rest_timestamp", "rest_snapshot_timestamp"))
    bid = numeric_value(data["order_book"], ("bid", "best_bid", "bid_price"))
    ask = numeric_value(data["order_book"], ("ask", "best_ask", "ask_price"))
    if bid is None or ask is None:
        bid = numeric_value(data["ticker"], ("bid", "best_bid", "bid_price"))
        ask = numeric_value(data["ticker"], ("ask", "best_ask", "ask_price"))
    spread_bps = calculate_spread_bps(bid, ask)
    top_depth = top_of_book_depth(data["order_book"])
    estimated_slippage = numeric_value(data["order_book"], ("estimated_slippage_bps", "slippage_bps"))
    if estimated_slippage is None:
        estimated_slippage = estimate_slippage_bps(spread_bps, top_depth, limits.min_top_depth)
    latency = max_numeric_value(
        [
            numeric_value(data["ticker"], ("latency_ms",)),
            numeric_value(data["order_book"], ("latency_ms",)),
            numeric_value(data["ws_heartbeat"], ("latency_ms",)),
            numeric_value(data["rest_snapshot"], ("latency_ms",)),
        ]
    )
    ws_rest_delta = abs((ws_ts - rest_ts).total_seconds()) if ws_ts and rest_ts else np.nan
    liquidity_score = float(min(top_depth / limits.min_top_depth, 1.0)) if top_depth is not None and limits.min_top_depth > 0 else np.nan
    return {
        "last_candle_age_seconds": safe_number(candle_age),
        "last_ticker_age_seconds": safe_number(ticker_age),
        "last_order_book_age_seconds": safe_number(order_book_age),
        "ws_heartbeat_age_seconds": safe_number(ws_age),
        "rest_snapshot_age_seconds": safe_number(rest_age),
        "ws_rest_timestamp_delta_seconds": safe_number(ws_rest_delta),
        "spread_bps": safe_number(spread_bps),
        "top_of_book_depth": safe_number(top_depth),
        "estimated_slippage_bps": safe_number(estimated_slippage),
        "liquidity_score": safe_number(liquidity_score),
        "latency_ms": safe_number(latency),
        "stale_data_count": 0,
        "divergence_count": 0,
    }


def data_freshness_guard(symbol: str, metrics: dict[str, Any], limits: MarketDataHealthLimits, *, strict: bool) -> dict[str, Any]:
    reasons: list[str] = []
    warnings: list[str] = []
    check_age(metrics, "last_candle_age_seconds", limits.max_candle_age_seconds, "candle_stale", reasons, warnings, strict)
    check_age(metrics, "last_ticker_age_seconds", limits.max_ticker_age_seconds, "ticker_stale", reasons, warnings, strict)
    check_age(metrics, "ws_heartbeat_age_seconds", limits.max_ws_heartbeat_age_seconds, "ws_heartbeat_stale", reasons, warnings, strict)
    return guard_payload("DataFreshnessGuard", symbol, reasons, warnings)


def order_book_guard(symbol: str, metrics: dict[str, Any], limits: MarketDataHealthLimits) -> dict[str, Any]:
    reasons: list[str] = []
    warnings: list[str] = []
    check_age(metrics, "last_order_book_age_seconds", limits.max_order_book_age_seconds, "order_book_stale", reasons, warnings, False)
    return guard_payload("OrderBookGuard", symbol, reasons, warnings)


def spread_guard(symbol: str, metrics: dict[str, Any], limits: MarketDataHealthLimits) -> dict[str, Any]:
    spread = metrics["spread_bps"]
    if is_missing(spread):
        return guard_payload("SpreadGuard", symbol, [], ["spread_missing"], missing=True)
    reasons = ["spread_bps_above_limit"] if float(spread) > limits.max_spread_bps else []
    return guard_payload("SpreadGuard", symbol, reasons, [])


def liquidity_guard(symbol: str, metrics: dict[str, Any], limits: MarketDataHealthLimits) -> dict[str, Any]:
    reasons: list[str] = []
    warnings: list[str] = []
    depth = metrics["top_of_book_depth"]
    slippage = metrics["estimated_slippage_bps"]
    if is_missing(depth):
        warnings.append("top_of_book_depth_missing")
    elif float(depth) < limits.min_top_depth:
        reasons.append("top_of_book_depth_below_min")
    if is_missing(slippage):
        warnings.append("estimated_slippage_missing")
    elif float(slippage) > limits.max_slippage_bps:
        reasons.append("estimated_slippage_bps_above_limit")
    return guard_payload("LiquidityGuard", symbol, reasons, warnings, missing=bool(warnings and not reasons))


def latency_guard(symbol: str, metrics: dict[str, Any], limits: MarketDataHealthLimits) -> dict[str, Any]:
    latency = metrics["latency_ms"]
    if is_missing(latency):
        return guard_payload("LatencyGuard", symbol, [], ["latency_missing"], missing=True)
    reasons = ["latency_ms_above_limit"] if float(latency) > limits.max_latency_ms else []
    return guard_payload("LatencyGuard", symbol, reasons, [])


def ws_rest_divergence_guard(symbol: str, metrics: dict[str, Any], limits: MarketDataHealthLimits, *, strict: bool) -> dict[str, Any]:
    delta = metrics["ws_rest_timestamp_delta_seconds"]
    if is_missing(delta):
        reason = ["ws_rest_timestamp_missing"] if strict else []
        warning = [] if strict else ["ws_rest_timestamp_missing"]
        return guard_payload("WsRestDivergenceGuard", symbol, reason, warning, missing=not strict)
    reasons = ["ws_rest_timestamp_delta_above_limit"] if float(delta) > limits.max_ws_rest_delta_seconds else []
    return guard_payload("WsRestDivergenceGuard", symbol, reasons, [])


def guard_payload(
    guard: str,
    symbol: str,
    reasons: list[str],
    warnings: list[str],
    *,
    missing: bool = False,
) -> dict[str, Any]:
    if reasons:
        status = "blocked"
    elif warnings:
        status = "missing_data" if missing else "warning"
    else:
        status = "ok"
    return {"guard": guard, "symbol": symbol, "status": status, "reasons": reasons, "warnings": warnings}


def check_age(
    metrics: dict[str, Any],
    key: str,
    limit: float,
    reason: str,
    reasons: list[str],
    warnings: list[str],
    strict: bool,
) -> None:
    age = metrics[key]
    if is_missing(age):
        if strict:
            reasons.append(f"{key}_missing")
        else:
            warnings.append(f"{key}_missing")
    elif float(age) > float(limit):
        reasons.append(reason)


def global_status(
    *,
    validation_errors: list[str],
    warnings: list[str],
    blocked_symbols: list[str],
    missing_data: list[str],
    strict: bool,
) -> str:
    if validation_errors or blocked_symbols:
        return "blocked"
    if strict and missing_data:
        return "blocked"
    if warnings or missing_data:
        return "warning"
    return "ok"


def global_summary(symbol_results: dict[str, Any], sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    stale_data_count = sum(
        1
        for result in symbol_results.values()
        for guard in result["guard_results"]
        for reason in guard["reasons"]
        if "stale" in reason
    )
    divergence_count = sum(
        1
        for result in symbol_results.values()
        for guard in result["guard_results"]
        for reason in guard["reasons"]
        if "ws_rest" in reason
    )
    return {
        "symbols_count": len(symbol_results),
        "blocked_symbols_count": sum(1 for result in symbol_results.values() if result["status"] == "blocked"),
        "warning_symbols_count": sum(1 for result in symbol_results.values() if result["status"] == "warning"),
        "missing_data_symbols_count": sum(1 for result in symbol_results.values() if result["status"] == "missing_data"),
        "stale_data_count": stale_data_count,
        "divergence_count": divergence_count,
        "sources_present": sorted(name for name, source in sources.items() if source["exists"]),
        "sources_missing": sorted(name for name, source in sources.items() if not source["exists"]),
    }


def source_summary(sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        name: {
            "path": source["path"],
            "exists": source["exists"],
            "status": source["status"],
            "rows": int(len(source["frame"])) if isinstance(source.get("frame"), pd.DataFrame) else 0,
            "error": source.get("error"),
        }
        for name, source in sources.items()
    }


def resolve_timestamp_column(frame: pd.DataFrame, preferred: str) -> str | None:
    return first_existing(
        frame,
        (
            preferred,
            "timestamp",
            "timestamp_utc",
            "ts",
            "time",
            "created_at",
            "generated_at_utc",
            "open_time_utc",
            "date",
        ),
    )


def row_age(row: dict[str, Any] | None, timestamp_column: str, now: datetime, fallbacks: tuple[str, ...] = ()) -> float:
    timestamp = row_timestamp(row, timestamp_column, fallbacks=fallbacks)
    if timestamp is None:
        return np.nan
    return max((now - timestamp).total_seconds(), 0.0)


def row_timestamp(row: dict[str, Any] | None, timestamp_column: str, fallbacks: tuple[str, ...] = ()) -> datetime | None:
    if not row:
        return None
    for key in (timestamp_column, *fallbacks, "_parsed_timestamp", "timestamp", "timestamp_utc", "created_at", "generated_at_utc"):
        if key in row and row[key] not in (None, ""):
            parsed = parse_timestamp(row[key])
            if parsed is not None:
                return parsed
    return None


def parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return ensure_utc(value.to_pydatetime())
    if isinstance(value, datetime):
        return ensure_utc(value)
    try:
        parsed = pd.to_datetime(value, utc=True, errors="coerce")
    except Exception:
        return None
    if pd.isna(parsed):
        return None
    return ensure_utc(pd.Timestamp(parsed).to_pydatetime())


def numeric_value(row: dict[str, Any] | None, keys: tuple[str, ...]) -> float | None:
    if not row:
        return None
    for key in keys:
        if key in row and row[key] not in (None, ""):
            try:
                value = float(row[key])
            except (TypeError, ValueError):
                continue
            if np.isfinite(value):
                return value
    return None


def max_numeric_value(values: list[float | None]) -> float:
    valid = [float(value) for value in values if value is not None and np.isfinite(value)]
    return max(valid) if valid else np.nan


def calculate_spread_bps(bid: float | None, ask: float | None) -> float:
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return np.nan
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return np.nan
    return ((ask - bid) / mid) * 10_000.0


def top_of_book_depth(row: dict[str, Any] | None) -> float | None:
    explicit = numeric_value(row, ("top_of_book_depth", "depth", "liquidity_usdt"))
    if explicit is not None:
        return explicit
    bid_size = numeric_value(row, ("bid_size", "best_bid_size", "bid_qty"))
    ask_size = numeric_value(row, ("ask_size", "best_ask_size", "ask_qty"))
    if bid_size is not None and ask_size is not None:
        return bid_size + ask_size
    return None


def estimate_slippage_bps(spread_bps: float, depth: float | None, min_depth: float) -> float:
    if is_missing(spread_bps):
        return np.nan
    if depth is None or not np.isfinite(depth) or depth <= 0:
        return np.nan
    pressure = max(min_depth / depth, 1.0) if min_depth > 0 else 1.0
    return float(spread_bps) * pressure


def safe_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(value):
        return None
    return value


def is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and not np.isfinite(value))


def first_existing(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    return next((column for column in candidates if column in frame.columns), None)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def utc_timestamp(value: datetime | None = None) -> str:
    return ensure_utc(value or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")


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
    for key in SAFE_FALSE_FLAGS:
        if payload.get(key) is True:
            unsafe.append(key)
    return unsafe


def write_json_if_requested(payload: dict[str, Any], path: Path | None) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True, default=str),
        encoding="utf-8",
    )
