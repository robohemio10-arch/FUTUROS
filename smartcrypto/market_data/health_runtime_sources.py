from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT")
DEFAULT_OUTPUT_DIR = Path("data/runtime/market_health")
DEFAULT_REPORT_PATH = Path("data/reports/market_data_health_runtime_sources_report.json")
BINANCE_FUTURES_REST_BASE = "https://fapi.binance.com"
SAFE_FALSE_FLAGS = (
    "live_trading_enabled",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "changes_risk",
)
Fetcher = Callable[[str, dict[str, Any], float], tuple[Any, float]]


@dataclass(frozen=True)
class RuntimeSourcePaths:
    ticker: Path
    order_book: Path
    trades: Path
    rest_snapshot: Path
    ws_heartbeat: Path

    def as_dict(self) -> dict[str, str]:
        return {key: str(value) for key, value in asdict(self).items()}


def collect_market_data_health_runtime_sources(
    *,
    symbols: list[str] | tuple[str, ...] = DEFAULT_SYMBOLS,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    report_path: str | Path | None = DEFAULT_REPORT_PATH,
    timeout_seconds: float = 5.0,
    strict: bool = False,
    fetcher: Fetcher | None = None,
    now: datetime | None = None,
    simulate_ws_heartbeat: bool = True,
    safety_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current_time = ensure_utc(now or datetime.now(timezone.utc))
    normalized_symbols = [normalize_symbol(symbol) for symbol in symbols]
    output = Path(output_dir)
    paths = RuntimeSourcePaths(
        ticker=output / "ticker.json",
        order_book=output / "order_book.json",
        trades=output / "trades.json",
        rest_snapshot=output / "rest_snapshot.json",
        ws_heartbeat=output / "ws_heartbeat.json",
    )
    active_fetcher = fetcher or public_binance_futures_fetcher
    safety = safety_payload(safety_overrides)
    blocking_errors = [f"unsafe_safety_flag:{flag}" for flag in unsafe_safety_flags(safety)]
    warnings: list[str] = []
    ticker_rows: list[dict[str, Any]] = []
    order_book_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    rest_rows: list[dict[str, Any]] = []
    heartbeat_rows: list[dict[str, Any]] = []

    for symbol in normalized_symbols:
        try:
            ticker_payload, ticker_latency = active_fetcher("/fapi/v1/ticker/bookTicker", {"symbol": symbol}, timeout_seconds)
            ticker_rows.append(ticker_row(symbol, ticker_payload, ticker_latency, current_time))
        except Exception as exc:
            warnings.append(f"{symbol}:ticker_fetch_failed:{type(exc).__name__}")
        try:
            depth_payload, depth_latency = active_fetcher("/fapi/v1/depth", {"symbol": symbol, "limit": 5}, timeout_seconds)
            order_book_rows.append(order_book_row(symbol, depth_payload, depth_latency, current_time))
        except Exception as exc:
            warnings.append(f"{symbol}:order_book_fetch_failed:{type(exc).__name__}")
        try:
            trades_payload, trades_latency = active_fetcher("/fapi/v1/trades", {"symbol": symbol, "limit": 5}, timeout_seconds)
            trade_rows.extend(trades_rows(symbol, trades_payload, trades_latency, current_time))
        except Exception as exc:
            warnings.append(f"{symbol}:trades_fetch_failed:{type(exc).__name__}")
        try:
            rest_payload, rest_latency = active_fetcher("/fapi/v1/time", {}, timeout_seconds)
            rest_rows.append(rest_snapshot_row(symbol, rest_payload, rest_latency, current_time))
        except Exception as exc:
            warnings.append(f"{symbol}:rest_snapshot_fetch_failed:{type(exc).__name__}")
        heartbeat_rows.append(ws_heartbeat_row(symbol, current_time, simulated=simulate_ws_heartbeat))

    if strict:
        for source_name, rows in {
            "ticker": ticker_rows,
            "order_book": order_book_rows,
            "trades": trade_rows,
            "rest_snapshot": rest_rows,
            "ws_heartbeat": heartbeat_rows,
        }.items():
            if not rows:
                blocking_errors.append(f"missing_runtime_source:{source_name}")
    write_runtime_json(paths.ticker, ticker_rows, current_time, safety)
    write_runtime_json(paths.order_book, order_book_rows, current_time, safety)
    write_runtime_json(paths.trades, trade_rows, current_time, safety)
    write_runtime_json(paths.rest_snapshot, rest_rows, current_time, safety)
    write_runtime_json(paths.ws_heartbeat, heartbeat_rows, current_time, safety)

    metrics = aggregate_metrics(ticker_rows, order_book_rows, rest_rows, heartbeat_rows, current_time)
    status = "blocked" if blocking_errors else "warning" if warnings else "ok"
    report = {
        "status": status,
        "reason": "ok" if status == "ok" else ";".join(sorted(set(blocking_errors or warnings))),
        "generated_at_utc": iso(current_time),
        "symbols": normalized_symbols,
        "exchange": "binance_usdt_m_futures_public",
        "public_data_only": True,
        "private_endpoints_used": False,
        "write_performed": True,
        "output_dir": str(output),
        "runtime_source_paths": paths.as_dict(),
        "source_counts": {
            "ticker": len(ticker_rows),
            "order_book": len(order_book_rows),
            "trades": len(trade_rows),
            "rest_snapshot": len(rest_rows),
            "ws_heartbeat": len(heartbeat_rows),
        },
        "metrics": metrics,
        "warnings": sorted(set(warnings)),
        "blocking_errors": sorted(set(blocking_errors)),
        **safety,
    }
    write_report(report, report_path)
    return report


def public_binance_futures_fetcher(endpoint: str, params: dict[str, Any], timeout_seconds: float) -> tuple[Any, float]:
    if not endpoint.startswith("/fapi/v1/"):
        raise ValueError(f"non_public_endpoint_blocked:{endpoint}")
    url = BINANCE_FUTURES_REST_BASE + endpoint
    query = urllib.parse.urlencode({key: value for key, value in params.items() if value is not None})
    if query:
        url = f"{url}?{query}"
    started = time.perf_counter()
    request = urllib.request.Request(url, headers={"User-Agent": "SmartCryptoPaperShadow/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError:
        raise
    latency_ms = (time.perf_counter() - started) * 1000.0
    return payload, latency_ms


def ticker_row(symbol: str, payload: Any, latency_ms: float, now: datetime) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    bid = float_value(data.get("bidPrice"))
    ask = float_value(data.get("askPrice"))
    return {
        "symbol": symbol,
        "timestamp": iso(now),
        "bid": bid,
        "ask": ask,
        "bid_size": float_value(data.get("bidQty")),
        "ask_size": float_value(data.get("askQty")),
        "spread_bps": spread_bps(bid, ask),
        "latency_ms": safe_float(latency_ms),
        "source": "binance_public_rest_book_ticker",
    }


def order_book_row(symbol: str, payload: Any, latency_ms: float, now: datetime) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    bids = data.get("bids") if isinstance(data.get("bids"), list) else []
    asks = data.get("asks") if isinstance(data.get("asks"), list) else []
    best_bid = price_level(bids, 0)
    best_ask = price_level(asks, 0)
    bid_price, bid_qty = best_bid
    ask_price, ask_qty = best_ask
    depth = top_depth(bids, asks)
    spread = spread_bps(bid_price, ask_price)
    return {
        "symbol": symbol,
        "timestamp": iso(now),
        "bid": bid_price,
        "ask": ask_price,
        "bid_size": bid_qty,
        "ask_size": ask_qty,
        "top_of_book_depth": depth,
        "spread_bps": spread,
        "estimated_slippage_bps": estimate_slippage_bps(spread, depth),
        "latency_ms": safe_float(latency_ms),
        "source": "binance_public_rest_depth",
    }


def trades_rows(symbol: str, payload: Any, latency_ms: float, now: datetime) -> list[dict[str, Any]]:
    rows = payload if isinstance(payload, list) else []
    output = []
    for trade in rows[:5]:
        if not isinstance(trade, dict):
            continue
        output.append(
            {
                "symbol": symbol,
                "timestamp": ms_to_iso(trade.get("time")) or iso(now),
                "price": float_value(trade.get("price")),
                "qty": float_value(trade.get("qty")),
                "trade_id": trade.get("id"),
                "latency_ms": safe_float(latency_ms),
                "source": "binance_public_rest_trades",
            }
        )
    return output


def rest_snapshot_row(symbol: str, payload: Any, latency_ms: float, now: datetime) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    server_time_iso = ms_to_iso(data.get("serverTime")) or iso(now)
    return {
        "symbol": symbol,
        "timestamp": server_time_iso,
        "rest_timestamp": server_time_iso,
        "latency_ms": safe_float(latency_ms),
        "source": "binance_public_rest_time",
    }


def ws_heartbeat_row(symbol: str, now: datetime, *, simulated: bool) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "timestamp": iso(now),
        "ws_timestamp": iso(now),
        "latency_ms": 0.0 if simulated else None,
        "simulated": bool(simulated),
        "paper_shadow_artifact": True,
        "source": "simulated_paper_shadow_ws_heartbeat" if simulated else "public_ws_heartbeat",
    }


def aggregate_metrics(
    ticker_rows: list[dict[str, Any]],
    order_book_rows: list[dict[str, Any]],
    rest_rows: list[dict[str, Any]],
    heartbeat_rows: list[dict[str, Any]],
    now: datetime,
) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    symbols = sorted({row["symbol"] for row in [*ticker_rows, *order_book_rows, *rest_rows, *heartbeat_rows]})
    for symbol in symbols:
        ticker = latest_for_symbol(ticker_rows, symbol)
        book = latest_for_symbol(order_book_rows, symbol)
        rest = latest_for_symbol(rest_rows, symbol)
        heartbeat = latest_for_symbol(heartbeat_rows, symbol)
        metrics[symbol] = {
            "spread_bps": first_present(book, ticker, "spread_bps"),
            "top_of_book_depth": value_or_none(book, "top_of_book_depth"),
            "estimated_slippage_bps": value_or_none(book, "estimated_slippage_bps"),
            "latency_ms": max(value for value in [value_or_none(ticker, "latency_ms"), value_or_none(book, "latency_ms"), value_or_none(rest, "latency_ms"), value_or_none(heartbeat, "latency_ms")] if value is not None) if any(value is not None for value in [value_or_none(ticker, "latency_ms"), value_or_none(book, "latency_ms"), value_or_none(rest, "latency_ms"), value_or_none(heartbeat, "latency_ms")]) else None,
            "last_ticker_age_seconds": age_seconds(ticker, now),
            "last_order_book_age_seconds": age_seconds(book, now),
            "rest_snapshot_age_seconds": age_seconds(rest, now),
            "ws_heartbeat_age_seconds": age_seconds(heartbeat, now),
            "ws_rest_timestamp_delta_seconds": timestamp_delta_seconds(heartbeat, rest),
        }
    return metrics


def write_runtime_json(path: Path, rows: list[dict[str, Any]], now: datetime, safety: dict[str, Any]) -> None:
    payload = {"generated_at_utc": iso(now), "rows": rows, **safety}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")


def write_report(report: dict[str, Any], report_path: str | Path | None) -> None:
    if report_path is None:
        return
    target = Path(report_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")


def normalize_symbol(symbol: str) -> str:
    return str(symbol).replace("/", "").replace(":USDT", "").upper().strip()


def price_level(levels: list[Any], index: int) -> tuple[float | None, float | None]:
    if len(levels) <= index or not isinstance(levels[index], list | tuple) or len(levels[index]) < 2:
        return None, None
    return float_value(levels[index][0]), float_value(levels[index][1])


def top_depth(bids: list[Any], asks: list[Any]) -> float | None:
    bid_price, bid_qty = price_level(bids, 0)
    ask_price, ask_qty = price_level(asks, 0)
    if bid_price is None or bid_qty is None or ask_price is None or ask_qty is None:
        return None
    return (bid_price * bid_qty) + (ask_price * ask_qty)


def spread_bps(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2.0
    return ((ask - bid) / mid) * 10_000.0 if mid > 0 else None


def estimate_slippage_bps(spread: float | None, depth: float | None, min_depth: float = 10_000.0) -> float | None:
    if spread is None or depth is None or depth <= 0:
        return None
    return spread * max(min_depth / depth, 1.0)


def float_value(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_float(value: Any) -> float | None:
    parsed = float_value(value)
    return parsed if parsed is not None and parsed >= 0 else None


def latest_for_symbol(rows: list[dict[str, Any]], symbol: str) -> dict[str, Any] | None:
    filtered = [row for row in rows if row.get("symbol") == symbol]
    return filtered[-1] if filtered else None


def value_or_none(row: dict[str, Any] | None, key: str) -> Any:
    return row.get(key) if row else None


def first_present(primary: dict[str, Any] | None, secondary: dict[str, Any] | None, key: str) -> Any:
    value = value_or_none(primary, key)
    return value if value is not None else value_or_none(secondary, key)


def age_seconds(row: dict[str, Any] | None, now: datetime) -> float | None:
    if not row:
        return None
    timestamp = parse_timestamp(row.get("timestamp"))
    return max((now - timestamp).total_seconds(), 0.0) if timestamp else None


def timestamp_delta_seconds(left: dict[str, Any] | None, right: dict[str, Any] | None) -> float | None:
    left_ts = parse_timestamp(value_or_none(left, "timestamp"))
    right_ts = parse_timestamp(value_or_none(right, "timestamp"))
    if not left_ts or not right_ts:
        return None
    return abs((left_ts - right_ts).total_seconds())


def parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return ensure_utc(value)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ensure_utc(parsed)


def ms_to_iso(value: Any) -> str | None:
    try:
        return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OSError):
        return None


def ensure_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def iso(value: datetime) -> str:
    return ensure_utc(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
        payload.update({key: value for key, value in overrides.items() if key in payload})
    return payload


def unsafe_safety_flags(payload: dict[str, Any]) -> list[str]:
    unsafe = []
    if payload.get("paper_only") is not True:
        unsafe.append("paper_only")
    if payload.get("shadow_only") is not True:
        unsafe.append("shadow_only")
    if payload.get("runtime_mode") != "paper":
        unsafe.append("runtime_mode")
    for flag in SAFE_FALSE_FLAGS:
        if payload.get(flag) is True:
            unsafe.append(flag)
    return unsafe
