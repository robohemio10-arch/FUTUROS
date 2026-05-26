from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover - exercised only in minimal runtimes.
    yaml = None  # type: ignore[assignment]

from smartcrypto.state.financial_event_log import (
    KNOWN_EVENT_TYPES,
    FinancialEventLogger,
)


TRUE_VALUES = {"1", "true", "yes", "y", "on"}
SAFE_RUNTIME_MODES = {"paper", "research", "shadow"}
HEALTHY = "HEALTHY"
DEGRADED = "DEGRADED"
BLOCKED = "BLOCKED"


class MarketDataHealthError(RuntimeError):
    pass


class MarketDataSafetyError(MarketDataHealthError):
    pass


class MarketDataBlockedError(MarketDataHealthError):
    pass


@dataclass(frozen=True)
class MarketDataHealthLimits:
    max_ticker_age_seconds: int = 30
    max_candle_age_seconds: int = 300
    max_spread_bps: float = 25.0
    min_liquidity_usdt: float = 10_000.0
    max_latency_ms: float = 1_000.0
    max_ws_rest_divergence_bps: float = 10.0
    degraded_ticker_age_seconds: int = 15
    degraded_candle_age_seconds: int = 180
    degraded_spread_bps: float = 15.0
    degraded_liquidity_usdt: float = 25_000.0
    degraded_latency_ms: float = 500.0
    degraded_ws_rest_divergence_bps: float = 5.0


@dataclass(frozen=True)
class MarketDataSnapshot:
    symbol: str
    ticker_timestamp_utc: str
    candle_timestamp_utc: str
    bid: float
    ask: float
    liquidity_usdt: float
    latency_ms: float
    ws_price: float
    rest_price: float
    source: str = "local_market_snapshot"

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MarketDataSnapshot":
        return cls(
            symbol=str(payload["symbol"]),
            ticker_timestamp_utc=str(payload["ticker_timestamp_utc"]),
            candle_timestamp_utc=str(payload["candle_timestamp_utc"]),
            bid=float(payload["bid"]),
            ask=float(payload["ask"]),
            liquidity_usdt=float(payload["liquidity_usdt"]),
            latency_ms=float(payload["latency_ms"]),
            ws_price=float(payload["ws_price"]),
            rest_price=float(payload["rest_price"]),
            source=str(payload.get("source", "local_market_snapshot")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketDataHealthResult:
    status: str
    block_decision: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    checked_at: str = field(default_factory=lambda: utc_timestamp())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MarketDataHealthGuard:
    def __init__(
        self,
        *,
        limits: MarketDataHealthLimits | None = None,
        event_logger: FinancialEventLogger | None = None,
        event_log_path: str | Path = "data/runtime/market_data_health_events.jsonl",
        runtime_mode: str = "paper",
    ) -> None:
        assert_market_runtime_safe(runtime_mode)
        self.runtime_mode = str(runtime_mode).strip().lower()
        self.limits = limits or MarketDataHealthLimits()
        self.event_logger = event_logger or FinancialEventLogger(
            event_log_path,
            runtime_mode=self.runtime_mode,
            source="market_data_health_guard",
            allowed_event_types=set(KNOWN_EVENT_TYPES),
        )

    @classmethod
    def from_config(cls, path: str | Path) -> "MarketDataHealthGuard":
        config = load_config(path)
        limits_payload = config.get("limits", {}) if isinstance(config.get("limits"), dict) else {}
        paths = config.get("paths", {}) if isinstance(config.get("paths"), dict) else {}
        return cls(
            limits=MarketDataHealthLimits(
                max_ticker_age_seconds=int(limits_payload.get("max_ticker_age_seconds", 30)),
                max_candle_age_seconds=int(limits_payload.get("max_candle_age_seconds", 300)),
                max_spread_bps=float(limits_payload.get("max_spread_bps", 25.0)),
                min_liquidity_usdt=float(limits_payload.get("min_liquidity_usdt", 10_000.0)),
                max_latency_ms=float(limits_payload.get("max_latency_ms", 1_000.0)),
                max_ws_rest_divergence_bps=float(
                    limits_payload.get("max_ws_rest_divergence_bps", 10.0)
                ),
                degraded_ticker_age_seconds=int(
                    limits_payload.get("degraded_ticker_age_seconds", 15)
                ),
                degraded_candle_age_seconds=int(
                    limits_payload.get("degraded_candle_age_seconds", 180)
                ),
                degraded_spread_bps=float(limits_payload.get("degraded_spread_bps", 15.0)),
                degraded_liquidity_usdt=float(
                    limits_payload.get("degraded_liquidity_usdt", 25_000.0)
                ),
                degraded_latency_ms=float(limits_payload.get("degraded_latency_ms", 500.0)),
                degraded_ws_rest_divergence_bps=float(
                    limits_payload.get("degraded_ws_rest_divergence_bps", 5.0)
                ),
            ),
            event_log_path=paths.get(
                "financial_event_log",
                "data/runtime/market_data_health_events.jsonl",
            ),
            runtime_mode=str(config.get("runtime_mode", "paper")),
        )

    def evaluate(
        self,
        snapshot: MarketDataSnapshot | dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> MarketDataHealthResult:
        assert_market_runtime_safe(self.runtime_mode)
        current_time = now or datetime.now(timezone.utc)
        market_snapshot = (
            snapshot
            if isinstance(snapshot, MarketDataSnapshot)
            else MarketDataSnapshot.from_dict(snapshot)
        )
        reasons: list[str] = []
        warnings: list[str] = []
        metrics = calculate_metrics(market_snapshot, current_time)
        self._evaluate_staleness(metrics, reasons, warnings)
        self._evaluate_quality(metrics, reasons, warnings)

        if reasons:
            status = BLOCKED
        elif warnings:
            status = DEGRADED
        else:
            status = HEALTHY

        result = MarketDataHealthResult(
            status=status,
            block_decision=status == BLOCKED,
            reasons=reasons,
            warnings=warnings,
            metrics=metrics,
        )
        self._record_result(market_snapshot, result)
        return result

    def assert_healthy_for_decision(
        self,
        snapshot: MarketDataSnapshot | dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> MarketDataHealthResult:
        result = self.evaluate(snapshot, now=now)
        if result.status == BLOCKED:
            raise MarketDataBlockedError(
                "market data health blocked decision: " + ",".join(result.reasons)
            )
        return result

    def _evaluate_staleness(
        self,
        metrics: dict[str, Any],
        reasons: list[str],
        warnings: list[str],
    ) -> None:
        if metrics["ticker_age_seconds"] > self.limits.max_ticker_age_seconds:
            reasons.append("stale_ticker")
        elif metrics["ticker_age_seconds"] > self.limits.degraded_ticker_age_seconds:
            warnings.append("ticker_age_degraded")

        if metrics["candle_age_seconds"] > self.limits.max_candle_age_seconds:
            reasons.append("stale_candle")
        elif metrics["candle_age_seconds"] > self.limits.degraded_candle_age_seconds:
            warnings.append("candle_age_degraded")

    def _evaluate_quality(
        self,
        metrics: dict[str, Any],
        reasons: list[str],
        warnings: list[str],
    ) -> None:
        if metrics["spread_bps"] > self.limits.max_spread_bps:
            reasons.append("spread_too_high")
        elif metrics["spread_bps"] > self.limits.degraded_spread_bps:
            warnings.append("spread_degraded")

        if metrics["liquidity_usdt"] < self.limits.min_liquidity_usdt:
            reasons.append("liquidity_too_low")
        elif metrics["liquidity_usdt"] < self.limits.degraded_liquidity_usdt:
            warnings.append("liquidity_degraded")

        if metrics["latency_ms"] > self.limits.max_latency_ms:
            reasons.append("latency_too_high")
        elif metrics["latency_ms"] > self.limits.degraded_latency_ms:
            warnings.append("latency_degraded")

        if metrics["ws_rest_divergence_bps"] > self.limits.max_ws_rest_divergence_bps:
            reasons.append("ws_rest_divergence_too_high")
        elif (
            metrics["ws_rest_divergence_bps"]
            > self.limits.degraded_ws_rest_divergence_bps
        ):
            warnings.append("ws_rest_divergence_degraded")

    def _record_result(
        self,
        snapshot: MarketDataSnapshot,
        result: MarketDataHealthResult,
    ) -> None:
        event_type = {
            HEALTHY: "market_data_healthy",
            DEGRADED: "market_data_degraded",
            BLOCKED: "market_data_blocked",
        }[result.status]
        self.event_logger.record(
            event_type,
            correlation_id=f"market-data-health-{snapshot.symbol}-{result.checked_at}",
            symbol=snapshot.symbol,
            payload={
                "status": result.status,
                "block_decision": result.block_decision,
                "reasons": result.reasons,
                "warnings": result.warnings,
                "metrics": result.metrics,
                "source": snapshot.source,
            },
        )


def calculate_metrics(snapshot: MarketDataSnapshot, now: datetime) -> dict[str, Any]:
    ticker_time = parse_utc(snapshot.ticker_timestamp_utc)
    candle_time = parse_utc(snapshot.candle_timestamp_utc)
    bid = float(snapshot.bid)
    ask = float(snapshot.ask)
    if bid <= 0 or ask <= 0 or ask < bid:
        raise MarketDataHealthError("invalid bid/ask values")
    mid = (bid + ask) / 2
    spread_bps = ((ask - bid) / mid) * 10_000
    ws_price = float(snapshot.ws_price)
    rest_price = float(snapshot.rest_price)
    if ws_price <= 0 or rest_price <= 0:
        raise MarketDataHealthError("invalid ws/rest price values")
    reference_price = (ws_price + rest_price) / 2
    divergence_bps = abs(ws_price - rest_price) / reference_price * 10_000
    return {
        "ticker_age_seconds": max(0.0, (now - ticker_time).total_seconds()),
        "candle_age_seconds": max(0.0, (now - candle_time).total_seconds()),
        "spread_bps": float(spread_bps),
        "liquidity_usdt": float(snapshot.liquidity_usdt),
        "latency_ms": float(snapshot.latency_ms),
        "ws_rest_divergence_bps": float(divergence_bps),
    }


def parse_utc(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MarketDataHealthError(f"invalid timestamp: {value}") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def env_enabled(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in TRUE_VALUES


def assert_market_runtime_safe(runtime_mode: str) -> None:
    normalized_mode = str(runtime_mode or "").strip().lower()
    reasons: list[str] = []
    if normalized_mode not in SAFE_RUNTIME_MODES:
        reasons.append(f"runtime_mode_not_allowed:{runtime_mode}")
    if env_enabled("LIVE_ENABLED"):
        reasons.append("LIVE_ENABLED=true")
    if env_enabled("ORDER_SUBMISSION_ENABLED"):
        reasons.append("ORDER_SUBMISSION_ENABLED=true")
    if env_enabled("REAL_ORDER_SUBMISSION_ENABLED"):
        reasons.append("REAL_ORDER_SUBMISSION_ENABLED=true")
    if reasons:
        raise MarketDataSafetyError("unsafe market data runtime: " + ",".join(reasons))


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_config(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {}
    text = target.read_text(encoding="utf-8")
    if target.suffix.lower() == ".json":
        payload = json.loads(text)
    elif yaml is not None:
        payload = yaml.safe_load(text) or {}
    else:
        payload = load_simple_yaml(text)
    if not isinstance(payload, dict):
        raise MarketDataHealthError(f"invalid config root: {target}")
    return payload


def load_simple_yaml(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.strip()
        if ":" not in stripped or stripped.startswith("- "):
            raise MarketDataHealthError("yaml fallback supports simple mappings only")
        key, raw_value = stripped.split(":", 1)
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if raw_value.strip() == "":
            child: dict[str, Any] = {}
            parent[key.strip()] = child
            stack.append((indent, child))
        else:
            parent[key.strip()] = parse_scalar(raw_value.strip())
    return root


def parse_scalar(value: str) -> Any:
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    lowered = value.lower()
    if lowered in TRUE_VALUES:
        return True
    if lowered in {"0", "false", "no", "n", "off", ""}:
        return False
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value
