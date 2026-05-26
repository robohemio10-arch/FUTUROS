from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from smartcrypto.config.schema import ConfigValidationError, load_config_file, validate_config
from smartcrypto.market.market_data_health_guard import (
    BLOCKED as MARKET_BLOCKED,
    MarketDataHealthGuard,
    MarketDataHealthLimits,
)
from smartcrypto.risk.kill_switch_guard import KillSwitchGuard
from smartcrypto.state.financial_event_log import KNOWN_EVENT_TYPES, FinancialEventLogger
from smartcrypto.state.reconciliation_guard import (
    CORRUPTED,
    DIVERGED,
    ReconciliationGuard,
)
from smartcrypto.state.state_repository import StateRepository, utc_timestamp


TRUE_VALUES = {"1", "true", "yes", "y", "on"}
SAFE_RUNTIME_MODES = {"paper", "research", "shadow"}
PASSED = "PASSED"
FAILED = "FAILED"
BLOCKED = "BLOCKED"


class RuntimePreflightError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimePreflightResult:
    status: str
    block_execution: bool
    checks: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    started_at: str = field(default_factory=utc_timestamp)
    completed_at: str = field(default_factory=utc_timestamp)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RuntimePreflightOrchestrator:
    def __init__(
        self,
        *,
        config_path: str | Path = "config/runtime_preflight.example.yml",
        event_logger: FinancialEventLogger | None = None,
        event_log_path: str | Path = "data/runtime/runtime_preflight_events.jsonl",
        kill_switch_path: str | Path = "data/runtime/kill_switch_guard.json",
        state_path: str | Path = "data/runtime/state_repository.json",
        runtime_mode: str = "paper",
    ) -> None:
        self.config_path = Path(config_path)
        self.runtime_mode = str(runtime_mode).strip().lower()
        self.state_path = Path(state_path)
        self.kill_switch_path = Path(kill_switch_path)
        self.event_log_path = Path(event_log_path)
        self._external_event_logger = event_logger is not None
        self.event_logger = event_logger or FinancialEventLogger(
            self.event_log_path,
            runtime_mode=self.runtime_mode,
            source="runtime_preflight_orchestrator",
            allowed_event_types=set(KNOWN_EVENT_TYPES),
        )

    def run(
        self,
        *,
        config: dict[str, Any] | None = None,
        market_snapshot: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> RuntimePreflightResult:
        started_at = utc_timestamp()
        early_block_reasons = runtime_guard_reasons(self.runtime_mode)
        if early_block_reasons:
            result = RuntimePreflightResult(
                status=BLOCKED,
                block_execution=True,
                errors=early_block_reasons,
                started_at=started_at,
                completed_at=utc_timestamp(),
            )
            self._record_runtime_guard_blocked(result)
            return result

        try:
            raw_config = config if config is not None else load_config_file(self.config_path)
        except Exception as exc:
            result = RuntimePreflightResult(
                status=FAILED,
                block_execution=True,
                errors=[f"config_load_failed:{exc}"],
                started_at=started_at,
                completed_at=utc_timestamp(),
            )
            self._record_failed(result)
            return result

        self._configure_event_logger_from_config(raw_config)
        self.event_logger.record(
            "runtime_preflight_started",
            correlation_id=f"runtime-preflight-{started_at}",
            payload={"config_path": str(self.config_path), "runtime_mode": self.runtime_mode},
        )

        try:
            safe_config = validate_config(raw_config)
        except ConfigValidationError as exc:
            result = RuntimePreflightResult(
                status=BLOCKED if has_runtime_config_error(exc.errors) else FAILED,
                block_execution=True,
                errors=exc.errors,
                started_at=started_at,
                completed_at=utc_timestamp(),
            )
            if result.status == BLOCKED:
                self._record_runtime_guard_blocked(result)
            else:
                self._record_failed(result)
            return result
        except Exception as exc:
            result = RuntimePreflightResult(
                status=FAILED,
                block_execution=True,
                errors=[f"config_load_failed:{exc}"],
                started_at=started_at,
                completed_at=utc_timestamp(),
            )
            self._record_failed(result)
            return result

        checks: dict[str, Any] = {"config": safe_config.to_dict()}
        kill_switch_path = resolve_path(raw_config, "kill_switch_state", self.kill_switch_path)
        kill_switch_guard = KillSwitchGuard(
            state_path=kill_switch_path,
            event_logger=self.event_logger,
            runtime_mode=safe_config.runtime_mode,
        )
        global_kill_result = kill_switch_guard.evaluate()
        checks["kill_switch"] = global_kill_result.to_dict()
        if global_kill_result.block_operation:
            result = RuntimePreflightResult(
                status=BLOCKED,
                block_execution=True,
                checks=checks,
                errors=[f"kill_switch_{global_kill_result.status.lower()}"],
                started_at=started_at,
                completed_at=utc_timestamp(),
            )
            self._record_failed(result)
            return result

        if market_snapshot is None:
            result = RuntimePreflightResult(
                status=FAILED,
                block_execution=True,
                checks=checks,
                errors=["market_snapshot_required"],
                started_at=started_at,
                completed_at=utc_timestamp(),
            )
            self._record_failed(result)
            return result

        symbol = extract_symbol(market_snapshot)
        symbol_kill_result = kill_switch_guard.evaluate(symbol)
        checks["kill_switch"] = symbol_kill_result.to_dict()
        if symbol_kill_result.block_operation:
            result = RuntimePreflightResult(
                status=BLOCKED,
                block_execution=True,
                checks=checks,
                errors=[f"kill_switch_{symbol_kill_result.status.lower()}"],
                started_at=started_at,
                completed_at=utc_timestamp(),
            )
            self._record_failed(result)
            return result

        state_path = resolve_path(raw_config, "state_repository", self.state_path)
        max_capital = float(safe_config.max_capital_global)
        repository = StateRepository(
            state_path,
            runtime_mode=safe_config.runtime_mode,
            max_capital_global=max_capital,
        )
        repository.load()

        market_guard = MarketDataHealthGuard(
            limits=market_limits(raw_config, safe_config),
            event_logger=self.event_logger,
            runtime_mode=safe_config.runtime_mode,
        )
        try:
            market_result = market_guard.evaluate(market_snapshot, now=now)
        except Exception as exc:
            result = RuntimePreflightResult(
                status=FAILED,
                block_execution=True,
                checks=checks,
                errors=[f"market_health_failed:{exc}"],
                started_at=started_at,
                completed_at=utc_timestamp(),
            )
            self._record_failed(result)
            return result
        checks["market_data"] = market_result.to_dict()
        if market_result.status == MARKET_BLOCKED:
            result = RuntimePreflightResult(
                status=BLOCKED,
                block_execution=True,
                checks=checks,
                errors=["market_data_blocked"],
                started_at=started_at,
                completed_at=utc_timestamp(),
            )
            self._record_failed(result)
            return result

        reconciliation_guard = ReconciliationGuard(
            repository=repository,
            event_logger=self.event_logger,
            runtime_mode=safe_config.runtime_mode,
            max_capital_global=max_capital,
        )
        reconciliation_result = reconciliation_guard.reconcile()
        checks["reconciliation"] = reconciliation_result.to_dict()
        if reconciliation_result.status in {DIVERGED, CORRUPTED}:
            result = RuntimePreflightResult(
                status=BLOCKED,
                block_execution=True,
                checks=checks,
                errors=[f"reconciliation_{reconciliation_result.status.lower()}"],
                started_at=started_at,
                completed_at=utc_timestamp(),
            )
            self._record_failed(result)
            return result

        result = RuntimePreflightResult(
            status=PASSED,
            block_execution=False,
            checks=checks,
            started_at=started_at,
            completed_at=utc_timestamp(),
        )
        self.event_logger.record(
            "runtime_preflight_passed",
            correlation_id=f"runtime-preflight-{started_at}",
            payload=result.to_dict(),
        )
        return result

    def _record_failed(self, result: RuntimePreflightResult) -> None:
        self.event_logger.record(
            "runtime_preflight_failed",
            correlation_id=f"runtime-preflight-{result.started_at}",
            payload=result.to_dict(),
        )

    def _record_runtime_guard_blocked(self, result: RuntimePreflightResult) -> None:
        self.event_logger.record(
            "runtime_guard_blocked",
            correlation_id=f"runtime-preflight-{result.started_at}",
            payload=result.to_dict(),
        )

    def _configure_event_logger_from_config(self, config: dict[str, Any]) -> None:
        if self._external_event_logger:
            return
        configured_path = resolve_path(config, "financial_event_log", self.event_log_path)
        if configured_path == self.event_log_path:
            return
        self.event_log_path = configured_path
        self.event_logger = FinancialEventLogger(
            self.event_log_path,
            runtime_mode=self.runtime_mode,
            source="runtime_preflight_orchestrator",
            allowed_event_types=set(KNOWN_EVENT_TYPES),
        )


def env_enabled(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in TRUE_VALUES


def runtime_guard_reasons(runtime_mode: str) -> list[str]:
    reasons: list[str] = []
    normalized = str(runtime_mode or "").strip().lower()
    if normalized not in SAFE_RUNTIME_MODES:
        reasons.append(f"runtime_mode_not_allowed:{runtime_mode}")
    if env_enabled("LIVE_ENABLED"):
        reasons.append("LIVE_ENABLED=true")
    if env_enabled("ORDER_SUBMISSION_ENABLED"):
        reasons.append("ORDER_SUBMISSION_ENABLED=true")
    if env_enabled("REAL_ORDER_SUBMISSION_ENABLED"):
        reasons.append("REAL_ORDER_SUBMISSION_ENABLED=true")
    return reasons


def has_runtime_config_error(errors: list[str]) -> bool:
    runtime_markers = (
        "runtime_mode_not_allowed",
        "live_enabled_must_be_false",
        "order_submission_enabled_must_be_false",
        "real_order_submission_enabled_must_be_false",
    )
    return any(any(marker in error for marker in runtime_markers) for error in errors)


def market_limits(config: dict[str, Any], safe_config: Any) -> MarketDataHealthLimits:
    payload = config.get("market_data") if isinstance(config, dict) else None
    market_config = payload if isinstance(payload, dict) else {}
    max_data_age = int(safe_config.max_data_age_seconds)
    degraded_age = max(1, int(max_data_age * 0.6))
    max_spread = float(safe_config.max_spread_bps)
    degraded_spread = max_spread * 0.6
    return MarketDataHealthLimits(
        max_ticker_age_seconds=max_data_age,
        max_candle_age_seconds=max_data_age,
        max_spread_bps=max_spread,
        min_liquidity_usdt=float(market_config.get("min_liquidity_usdt", 10_000.0)),
        max_latency_ms=float(market_config.get("max_latency_ms", 1_000.0)),
        max_ws_rest_divergence_bps=float(
            market_config.get("max_ws_rest_divergence_bps", 10.0)
        ),
        degraded_ticker_age_seconds=degraded_age,
        degraded_candle_age_seconds=degraded_age,
        degraded_spread_bps=degraded_spread,
        degraded_liquidity_usdt=float(
            market_config.get("degraded_liquidity_usdt", 25_000.0)
        ),
        degraded_latency_ms=float(market_config.get("degraded_latency_ms", 500.0)),
        degraded_ws_rest_divergence_bps=float(
            market_config.get("degraded_ws_rest_divergence_bps", 5.0)
        ),
    )


def resolve_path(config: dict[str, Any], key: str, default: str | Path) -> Path:
    paths = config.get("paths") if isinstance(config, dict) else None
    if isinstance(paths, dict) and paths.get(key):
        return Path(str(paths[key]))
    return Path(default)


def extract_symbol(market_snapshot: dict[str, Any]) -> str | None:
    value = market_snapshot.get("symbol") if isinstance(market_snapshot, dict) else None
    text = str(value or "").strip().upper()
    return text or None
