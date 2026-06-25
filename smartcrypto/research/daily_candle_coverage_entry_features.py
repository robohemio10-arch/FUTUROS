"""Research-only candle coverage and pre-entry feature calculations."""

from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from smartcrypto.research.daily_learning_contracts import SAFETY_FLAGS


DAILY_CANDLE_COVERAGE_ENTRY_FEATURES_SCHEMA_VERSION = (
    "daily_candle_coverage_entry_features_v1"
)
DEFAULT_LOOKBACK_WINDOWS_MINUTES = (5, 10, 30)
DEFAULT_TIMEFRAME_SECONDS = 15
MAX_SAMPLE_ROWS = 20

FEATURE_SCOPE: dict[str, bool] = {
    "computes_candle_coverage": True,
    "computes_entry_features": True,
    "loads_runtime_trade_rows": False,
    "loads_excel_rows": False,
    "loads_sqlite_rows": False,
    "loads_real_candle_rows": False,
    "uses_only_in_memory_inputs": True,
    "computes_labels": False,
    "uses_net_pnl_as_feature": False,
    "mines_patterns": False,
    "registers_candidate_rules": False,
    "runs_oos_validation": False,
    "updates_models": False,
    "updates_risk": False,
    "updates_execution": False,
    "writes_reports": False,
}

READINESS_POLICY: dict[str, bool] = {
    "candle_features_are_not_readiness_evidence": True,
    "candle_features_do_not_release_live": True,
    "candle_features_do_not_release_canary": True,
    "manual_go_no_go_required": True,
    "thirty_day_gap_free_soak_required_for_future_canary_review": True,
}

ALLOWED_NEXT_STEPS = [
    "criar mistake/winner catalog em branch futura",
    "criar pattern mining research em branch futura",
    "criar candidate shadow rule registry em branch futura",
    "criar OOS validation em branch futura",
    "criar AI Shadow feedback bridge em branch futura",
]

FORBIDDEN_ACTIONS = [
    "alterar Freqtrade",
    "alterar RiskManager",
    "alterar Qlib runtime",
    "alterar IA Shadow runtime",
    "alterar modelos",
    "alterar datasets",
    "habilitar live",
    "habilitar canary",
    "enviar ordem real",
    "usar exchange privada",
    "escrever artefatos em data/runtime/reports/logs/freqtrade",
    "usar features para liberar operacao",
    "promover regra candidata",
    "promover modelo",
    "criar regras candidatas nesta branch",
    "usar net_pnl como feature",
]


def build_daily_candle_coverage_entry_features_report(
    project_root: str | Path | None = None,
    trades: Sequence[Mapping[str, Any]] | None = None,
    candles_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    lookback_windows_minutes: Sequence[int] | None = None,
    timeframe_seconds: int = DEFAULT_TIMEFRAME_SECONDS,
) -> dict[str, Any]:
    """Build a blocked report from in-memory trades and candles only."""
    root = Path("." if project_root is None else project_root).expanduser().resolve()
    trade_rows = [] if trades is None else list(trades)
    candle_rows = {} if candles_by_symbol is None else dict(candles_by_symbol)
    windows = _normalize_windows(lookback_windows_minutes)
    timeframe = timeframe_seconds if timeframe_seconds > 0 else DEFAULT_TIMEFRAME_SECONDS
    input_mode = (
        "no_runtime_rows_loaded"
        if trades is None and candles_by_symbol is None
        else "in_memory_trade_and_candle_rows"
    )
    payload: dict[str, Any] = {
        "schema_version": DAILY_CANDLE_COVERAGE_ENTRY_FEATURES_SCHEMA_VERSION,
        "project_name": "SMART FUTUROS",
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "reason": "candle_coverage_entry_features_research_only_without_operational_authority",
        "project_root": str(root),
        **SAFETY_FLAGS,
        "input_mode": input_mode,
        "lookback_windows_minutes": list(windows),
        "timeframe_seconds": timeframe,
        "coverage": calculate_candle_coverage(
            trade_rows,
            candle_rows,
            windows,
            timeframe,
        ),
        "entry_features": materialize_entry_features(
            trade_rows,
            candle_rows,
            windows,
            timeframe,
        ),
        "feature_scope": dict(FEATURE_SCOPE),
        "readiness_policy": dict(READINESS_POLICY),
        "allowed_next_steps": list(ALLOWED_NEXT_STEPS),
        "forbidden_actions": list(FORBIDDEN_ACTIONS),
        "operator_decision": {
            "live_release_allowed": False,
            "canary_release_allowed": False,
            "freqtrade_strategy_change_allowed": False,
            "risk_manager_change_allowed": False,
            "model_promotion_allowed": False,
            "shadow_rule_promotion_allowed": False,
            "final_decision": "BLOQUEADO_PARA_OPERACAO_MANTER_EM_RESEARCH",
        },
        "write_requested": False,
        "write_performed": False,
    }
    payload["validation_errors"] = validate_daily_candle_coverage_entry_features_report(
        payload
    )
    return payload


def calculate_candle_coverage(
    trades: Sequence[Mapping[str, Any]],
    candles_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    lookback_windows_minutes: Sequence[int],
    timeframe_seconds: int,
) -> dict[str, Any]:
    """Calculate per-trade candle availability without loading external data."""
    windows = _normalize_windows(lookback_windows_minutes)
    max_window = max(windows) if windows else 0
    candle_index = _normalized_candles_by_symbol(candles_by_symbol)
    normalized_trades = [
        normalize_trade_for_features(trade, index)
        for index, trade in enumerate(trades)
    ]
    coverage_by_symbol: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"trade_count": 0, "covered_trade_count": 0, "uncovered_trade_count": 0}
    )
    coverage_by_window = {
        str(window): {
            "window_minutes": window,
            "covered_trade_count": 0,
            "uncovered_trade_count": 0,
            "observed_candle_count": 0,
            "expected_candle_count": _expected_candle_count(window, timeframe_seconds),
            "coverage_ratio": None,
        }
        for window in windows
    }
    covered_ids: list[str] = []
    uncovered_ids: list[str] = []
    missing_symbol_count = 0
    missing_entry_time_count = 0
    missing_candle_symbol_count = 0
    for trade in normalized_trades:
        symbol = trade.get("symbol")
        entry_time = trade.get("entry_time")
        trade_id = str(trade["trade_id"])
        if not symbol:
            missing_symbol_count += 1
            uncovered_ids.append(trade_id)
            continue
        symbol_summary = coverage_by_symbol[str(symbol)]
        symbol_summary["trade_count"] += 1
        if not isinstance(entry_time, dt.datetime):
            missing_entry_time_count += 1
            symbol_summary["uncovered_trade_count"] += 1
            uncovered_ids.append(trade_id)
            continue
        candles = candle_index.get(str(symbol), [])
        if not candles:
            missing_candle_symbol_count += 1
        window_counts = {
            window: _candles_in_window(candles, entry_time, window)
            for window in windows
        }
        is_covered = bool(max_window and window_counts[max_window])
        for window, selected in window_counts.items():
            window_summary = coverage_by_window[str(window)]
            observed = len(selected)
            window_summary["observed_candle_count"] += observed
            if observed:
                window_summary["covered_trade_count"] += 1
            else:
                window_summary["uncovered_trade_count"] += 1
        if is_covered:
            symbol_summary["covered_trade_count"] += 1
            covered_ids.append(trade_id)
        else:
            symbol_summary["uncovered_trade_count"] += 1
            uncovered_ids.append(trade_id)
    for summary in coverage_by_symbol.values():
        summary["coverage_rate_pct"] = _rate(
            summary["covered_trade_count"],
            summary["trade_count"],
        )
    for summary in coverage_by_window.values():
        expected_total = summary["expected_candle_count"] * len(normalized_trades)
        summary["coverage_ratio"] = (
            summary["observed_candle_count"] / expected_total
            if expected_total > 0
            else None
        )
    trade_count = len(normalized_trades)
    covered_trade_count = len(covered_ids)
    return {
        "trade_count": trade_count,
        "covered_trade_count": covered_trade_count,
        "uncovered_trade_count": trade_count - covered_trade_count,
        "coverage_rate_pct": _rate(covered_trade_count, trade_count),
        "coverage_by_symbol": dict(sorted(coverage_by_symbol.items())),
        "coverage_by_window": coverage_by_window,
        "missing_symbol_count": missing_symbol_count,
        "missing_entry_time_count": missing_entry_time_count,
        "missing_candle_symbol_count": missing_candle_symbol_count,
        "covered_trade_ids_sample": covered_ids[:MAX_SAMPLE_ROWS],
        "uncovered_trade_ids_sample": uncovered_ids[:MAX_SAMPLE_ROWS],
    }


def materialize_entry_features(
    trades: Sequence[Mapping[str, Any]],
    candles_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    lookback_windows_minutes: Sequence[int],
    timeframe_seconds: int,
) -> dict[str, Any]:
    """Create deterministic pre-entry feature rows from in-memory candles."""
    windows = _normalize_windows(lookback_windows_minutes)
    candle_index = _normalized_candles_by_symbol(candles_by_symbol)
    feature_rows: list[dict[str, Any]] = []
    feature_columns = _feature_columns(windows)
    missing_feature_trade_count = 0
    for index, raw_trade in enumerate(trades):
        trade = normalize_trade_for_features(raw_trade, index)
        row = _feature_row_for_trade(trade, candle_index, windows, timeframe_seconds)
        if row is None:
            missing_feature_trade_count += 1
            continue
        feature_rows.append(row)
    return {
        "feature_row_count": len(feature_rows),
        "feature_rows_sample": feature_rows[:MAX_SAMPLE_ROWS],
        "feature_columns": feature_columns,
        "feature_summary": {
            "features_use_only_pre_entry_candles": True,
            "labels_created": False,
            "net_pnl_used_as_feature": False,
            "window_count": len(windows),
        },
        "missing_feature_trade_count": missing_feature_trade_count,
        "features_computed": True,
    }


def normalize_trade_for_features(
    trade: Mapping[str, Any],
    index: int,
) -> dict[str, Any]:
    """Normalize an in-memory trade for coverage and feature calculations."""
    entry_time = _parse_time(trade.get("open_time", trade.get("entry_time")))
    close_time = _parse_time(trade.get("close_time", trade.get("exit_time")))
    return {
        "trade_id": str(trade.get("trade_id") or f"trade_{index}"),
        "symbol": _normalize_symbol(trade.get("symbol")),
        "side": _normalize_side(trade.get("side")),
        "entry_time": entry_time,
        "entry_time_utc": _time_text(entry_time),
        "close_time": close_time,
        "close_time_utc": _time_text(close_time),
        "entry_price": _to_float(trade.get("entry_price")),
        "exit_price": _to_float(trade.get("exit_price")),
        "exit_reason": str(trade.get("exit_reason") or "").lower(),
        "duration_minutes": _to_float(trade.get("duration_minutes")),
    }


def normalize_candle(candle: Mapping[str, Any], index: int) -> dict[str, Any]:
    """Normalize a single in-memory candle."""
    timestamp = _parse_time(candle.get("timestamp", candle.get("open_time")))
    return {
        "index": index,
        "symbol": _normalize_symbol(candle.get("symbol")),
        "timestamp": timestamp,
        "timestamp_utc": _time_text(timestamp),
        "open": _to_float(candle.get("open")),
        "high": _to_float(candle.get("high")),
        "low": _to_float(candle.get("low")),
        "close": _to_float(candle.get("close")),
        "volume": _to_float(candle.get("volume")),
    }


def validate_daily_candle_coverage_entry_features_report(
    payload: Mapping[str, Any],
) -> list[str]:
    """Validate the candle coverage and feature report contract."""
    errors: list[str] = []
    expected_header: dict[str, Any] = {
        "schema_version": DAILY_CANDLE_COVERAGE_ENTRY_FEATURES_SCHEMA_VERSION,
        "project_name": "SMART FUTUROS",
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "reason": "candle_coverage_entry_features_research_only_without_operational_authority",
    }
    for key, expected in expected_header.items():
        if payload.get(key) != expected:
            errors.append(f"{key}_mismatch")
    for key, expected in SAFETY_FLAGS.items():
        if payload.get(key) is not expected:
            errors.append(f"{key}_must_be_{str(expected).lower()}")
    scope = _mapping(payload.get("feature_scope"))
    for key, expected in FEATURE_SCOPE.items():
        if scope.get(key) is not expected:
            errors.append(f"feature_scope_{key}_mismatch")
    readiness = _mapping(payload.get("readiness_policy"))
    for key, expected in READINESS_POLICY.items():
        if readiness.get(key) is not expected:
            errors.append(f"readiness_policy_{key}_mismatch")
    if not isinstance(payload.get("coverage"), Mapping):
        errors.append("coverage_must_be_object")
    if not isinstance(payload.get("entry_features"), Mapping):
        errors.append("entry_features_must_be_object")
    return errors


def _feature_row_for_trade(
    trade: Mapping[str, Any],
    candle_index: Mapping[str, Sequence[Mapping[str, Any]]],
    windows: tuple[int, ...],
    timeframe_seconds: int,
) -> dict[str, Any] | None:
    symbol = trade.get("symbol")
    entry_time = trade.get("entry_time")
    if not symbol or not isinstance(entry_time, dt.datetime):
        return None
    candles = list(candle_index.get(str(symbol), []))
    pre_entry = [
        candle
        for candle in candles
        if isinstance(candle.get("timestamp"), dt.datetime)
        and candle["timestamp"] <= entry_time
    ]
    if not pre_entry:
        return None
    entry_candle = pre_entry[-1]
    row: dict[str, Any] = {
        "trade_id": trade.get("trade_id"),
        "symbol": symbol,
        "side": trade.get("side"),
        "entry_time": trade.get("entry_time_utc"),
        "has_entry_candle": True,
        "max_lookback_covered": False,
        "entry_close": entry_candle.get("close"),
        "entry_open": entry_candle.get("open"),
        "entry_high": entry_candle.get("high"),
        "entry_low": entry_candle.get("low"),
        "entry_volume": entry_candle.get("volume"),
        "entry_return_1_candle": _entry_return_1_candle(pre_entry),
        "sma_20": _sma(pre_entry, 20),
        "dist_sma_20_pct": None,
        "rsi_14": _rsi_14(pre_entry),
        "pre_entry_volatility_20": _volatility_20(pre_entry),
    }
    if row["sma_20"] not in (None, 0) and row["entry_close"] is not None:
        row["dist_sma_20_pct"] = (row["entry_close"] / row["sma_20"]) - 1
    coverage_flags: list[bool] = []
    for window in windows:
        selected = _candles_in_window(pre_entry, entry_time, window)
        expected = _expected_candle_count(window, timeframe_seconds)
        coverage_flags.append(bool(selected))
        prefix = f"lb_{window}m"
        row[f"{prefix}_candle_count"] = len(selected)
        row[f"{prefix}_expected_candle_count"] = expected
        row[f"{prefix}_coverage_ratio"] = len(selected) / expected if expected else None
        row[f"{prefix}_ret_close"] = _ret_close(selected)
        row[f"{prefix}_high_low_range_pct"] = _high_low_range_pct(selected)
        row[f"{prefix}_volume_sum"] = _volume_sum(selected)
    row["max_lookback_covered"] = all(coverage_flags) if coverage_flags else False
    return row


def _normalized_candles_by_symbol(
    candles_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for raw_symbol, candles in candles_by_symbol.items():
        symbol = _normalize_symbol(raw_symbol)
        if not symbol:
            continue
        normalized: list[dict[str, Any]] = []
        for index, candle in enumerate(candles):
            item = normalize_candle(candle, index)
            item["symbol"] = item.get("symbol") or symbol
            if item["timestamp"] is not None:
                normalized.append(item)
        result[symbol] = sorted(normalized, key=lambda item: item["timestamp"])
    return result


def _candles_in_window(
    candles: Sequence[Mapping[str, Any]],
    entry_time: dt.datetime,
    window_minutes: int,
) -> list[Mapping[str, Any]]:
    start = entry_time - dt.timedelta(minutes=window_minutes)
    return [
        candle
        for candle in candles
        if isinstance(candle.get("timestamp"), dt.datetime)
        and start <= candle["timestamp"] <= entry_time
    ]


def _feature_columns(windows: tuple[int, ...]) -> list[str]:
    columns = [
        "trade_id",
        "symbol",
        "side",
        "entry_time",
        "has_entry_candle",
        "max_lookback_covered",
        "entry_close",
        "entry_open",
        "entry_high",
        "entry_low",
        "entry_volume",
        "entry_return_1_candle",
        "sma_20",
        "dist_sma_20_pct",
        "rsi_14",
        "pre_entry_volatility_20",
    ]
    for window in windows:
        prefix = f"lb_{window}m"
        columns.extend(
            [
                f"{prefix}_candle_count",
                f"{prefix}_expected_candle_count",
                f"{prefix}_coverage_ratio",
                f"{prefix}_ret_close",
                f"{prefix}_high_low_range_pct",
                f"{prefix}_volume_sum",
            ]
        )
    return columns


def _ret_close(candles: Sequence[Mapping[str, Any]]) -> float | None:
    closes = [candle.get("close") for candle in candles if candle.get("close") is not None]
    if len(closes) < 2 or closes[0] == 0:
        return None
    return (closes[-1] / closes[0]) - 1


def _high_low_range_pct(candles: Sequence[Mapping[str, Any]]) -> float | None:
    highs = [candle.get("high") for candle in candles if candle.get("high") is not None]
    lows = [candle.get("low") for candle in candles if candle.get("low") is not None]
    if not highs or not lows or min(lows) == 0:
        return None
    return (max(highs) / min(lows)) - 1


def _volume_sum(candles: Sequence[Mapping[str, Any]]) -> float:
    return sum(
        float(candle["volume"])
        for candle in candles
        if candle.get("volume") is not None
    )


def _entry_return_1_candle(candles: Sequence[Mapping[str, Any]]) -> float | None:
    if len(candles) < 2:
        return None
    previous = candles[-2].get("close")
    current = candles[-1].get("close")
    if previous in (None, 0) or current is None:
        return None
    return (current / previous) - 1


def _sma(candles: Sequence[Mapping[str, Any]], count: int) -> float | None:
    closes = [candle.get("close") for candle in candles if candle.get("close") is not None]
    if len(closes) < count:
        return None
    selected = closes[-count:]
    return sum(selected) / count


def _rsi_14(candles: Sequence[Mapping[str, Any]]) -> float | None:
    closes = [candle.get("close") for candle in candles if candle.get("close") is not None]
    if len(closes) < 15:
        return None
    selected = closes[-15:]
    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(selected, selected[1:], strict=False):
        delta = current - previous
        gains.append(max(delta, 0.0))
        losses.append(abs(min(delta, 0.0)))
    average_gain = sum(gains) / 14
    average_loss = sum(losses) / 14
    if average_loss == 0:
        return 100.0
    relative_strength = average_gain / average_loss
    return 100 - (100 / (1 + relative_strength))


def _volatility_20(candles: Sequence[Mapping[str, Any]]) -> float | None:
    closes = [candle.get("close") for candle in candles if candle.get("close") is not None]
    if len(closes) < 21:
        return None
    selected = closes[-21:]
    returns = [
        (current / previous) - 1
        for previous, current in zip(selected, selected[1:], strict=False)
        if previous != 0
    ]
    if not returns:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / len(returns)
    return math.sqrt(variance)


def _expected_candle_count(window_minutes: int, timeframe_seconds: int) -> int:
    if timeframe_seconds <= 0:
        return 0
    return math.floor((window_minutes * 60) / timeframe_seconds) + 1


def _normalize_windows(windows: Sequence[int] | None) -> tuple[int, ...]:
    raw = DEFAULT_LOOKBACK_WINDOWS_MINUTES if windows is None else windows
    normalized = sorted({int(window) for window in raw if int(window) > 0})
    return tuple(normalized) or DEFAULT_LOOKBACK_WINDOWS_MINUTES


def _normalize_side(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if text in {"long", "buy"}:
        return "long"
    if text in {"short", "sell"}:
        return "short"
    return None


def _normalize_symbol(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper().replace("/", "")
    return text or None


def _parse_time(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def _time_text(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _rate(count: int, total: int) -> float | None:
    if total <= 0:
        return None
    return (count / total) * 100.0


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
