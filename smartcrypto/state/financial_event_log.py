from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover - only used in minimal runtimes without PyYAML.
    yaml = None  # type: ignore[assignment]


TRUE_VALUES = {"1", "true", "yes", "y", "on"}
SAFE_RUNTIME_MODES = {"paper", "research", "shadow"}
MINIMUM_EVENT_TYPES = {
    "signal_generated",
    "risk_approved",
    "risk_rejected",
    "paper_trade_adjusted",
    "emergency_stop_triggered",
    "kill_switch_triggered",
    "runtime_guard_blocked",
}
RECONCILIATION_EVENT_TYPES = {
    "state_reconciled",
    "state_divergence_detected",
    "reconciliation_failed",
}
KNOWN_EVENT_TYPES = MINIMUM_EVENT_TYPES | RECONCILIATION_EVENT_TYPES
DEFAULT_LOG_PATH = "data/runtime/financial_event_log.jsonl"


class FinancialEventLogError(RuntimeError):
    """Base error for the institutional financial event log."""


class InvalidFinancialEvent(FinancialEventLogError):
    """Raised when an event does not satisfy the financial log contract."""


class FinancialEventWriteError(FinancialEventLogError):
    """Raised when the JSONL event cannot be persisted locally."""


@dataclass(frozen=True)
class RuntimeSafetySnapshot:
    runtime_mode: str = "paper"
    live_enabled: bool = False
    order_submission_enabled: bool = False
    real_order_submission_enabled: bool = False

    @classmethod
    def from_env(cls, runtime_mode: str | None = None) -> "RuntimeSafetySnapshot":
        return cls(
            runtime_mode=str(
                runtime_mode or os.getenv("SMARTCRYPTO_RUNTIME_MODE", "paper")
            ).strip()
            or "paper",
            live_enabled=env_enabled("LIVE_ENABLED"),
            order_submission_enabled=env_enabled("ORDER_SUBMISSION_ENABLED"),
            real_order_submission_enabled=env_enabled("REAL_ORDER_SUBMISSION_ENABLED"),
        )

    def validate_safe(self, allow_guard_block_event: bool = False) -> None:
        normalized_mode = self.runtime_mode.strip().lower()
        unsafe_reasons = []
        if normalized_mode not in SAFE_RUNTIME_MODES:
            unsafe_reasons.append(f"runtime_mode_not_allowed:{self.runtime_mode}")
        if self.live_enabled:
            unsafe_reasons.append("LIVE_ENABLED=true")
        if self.order_submission_enabled:
            unsafe_reasons.append("ORDER_SUBMISSION_ENABLED=true")
        if self.real_order_submission_enabled:
            unsafe_reasons.append("REAL_ORDER_SUBMISSION_ENABLED=true")

        if unsafe_reasons and not allow_guard_block_event:
            raise InvalidFinancialEvent(
                "unsafe runtime flags blocked for financial event log: "
                + ",".join(unsafe_reasons)
            )


@dataclass(frozen=True)
class FinancialEvent:
    event_id: str
    timestamp_utc: str
    event_type: str
    correlation_id: str
    symbol: str | None
    source: str
    runtime_mode: str
    live_enabled: bool
    order_submission_enabled: bool
    real_order_submission_enabled: bool
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json_line(self) -> str:
        validate_event_dict(self.to_dict())
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"


@dataclass(frozen=True)
class FinancialEventLogConfig:
    log_path: str = DEFAULT_LOG_PATH
    runtime_mode: str = "paper"
    source: str = "smartcrypto"
    allowed_event_types: tuple[str, ...] = tuple(sorted(MINIMUM_EVENT_TYPES))

    @classmethod
    def from_yaml(cls, path: str | Path) -> "FinancialEventLogConfig":
        target = Path(path)
        if not target.exists():
            return cls()
        if yaml is None:
            raise FinancialEventLogError("PyYAML is required to read financial event config")

        with target.open("r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}
        if not isinstance(payload, dict):
            raise FinancialEventLogError(f"Invalid financial event log config: {target}")

        events = payload.get("events", {}) if isinstance(payload.get("events"), dict) else {}
        raw_event_types = events.get("allowed_event_types", sorted(MINIMUM_EVENT_TYPES))
        if isinstance(raw_event_types, str):
            raw_event_types = [raw_event_types]

        return cls(
            log_path=str(payload.get("log_path", DEFAULT_LOG_PATH)),
            runtime_mode=str(payload.get("runtime_mode", "paper")),
            source=str(payload.get("source", "smartcrypto")),
            allowed_event_types=tuple(str(item) for item in raw_event_types),
        )


class FinancialEventLogger:
    def __init__(
        self,
        log_path: str | Path = DEFAULT_LOG_PATH,
        *,
        runtime_mode: str = "paper",
        source: str = "smartcrypto",
        allowed_event_types: set[str] | None = None,
    ) -> None:
        self.log_path = Path(log_path)
        self.runtime_mode = runtime_mode
        self.source = source
        self.allowed_event_types = set(allowed_event_types or MINIMUM_EVENT_TYPES)
        missing = MINIMUM_EVENT_TYPES - self.allowed_event_types
        if missing:
            raise InvalidFinancialEvent(
                f"allowed_event_types missing required events: {sorted(missing)}"
            )

    @classmethod
    def from_config(cls, path: str | Path) -> "FinancialEventLogger":
        config = FinancialEventLogConfig.from_yaml(path)
        return cls(
            log_path=config.log_path,
            runtime_mode=config.runtime_mode,
            source=config.source,
            allowed_event_types=set(config.allowed_event_types),
        )

    def record(
        self,
        event_type: str,
        *,
        correlation_id: str | None = None,
        symbol: str | None = None,
        source: str | None = None,
        payload: dict[str, Any] | None = None,
        runtime: RuntimeSafetySnapshot | None = None,
    ) -> FinancialEvent:
        if event_type not in self.allowed_event_types:
            raise InvalidFinancialEvent(f"Unsupported financial event_type: {event_type}")

        snapshot = runtime or RuntimeSafetySnapshot.from_env(self.runtime_mode)
        snapshot.validate_safe(allow_guard_block_event=event_type == "runtime_guard_blocked")
        event_id = str(uuid.uuid4())
        event = FinancialEvent(
            event_id=event_id,
            timestamp_utc=utc_timestamp(),
            event_type=event_type,
            correlation_id=correlation_id or event_id,
            symbol=normalize_symbol(symbol),
            source=str(source or self.source),
            runtime_mode=snapshot.runtime_mode,
            live_enabled=bool(snapshot.live_enabled),
            order_submission_enabled=bool(snapshot.order_submission_enabled),
            real_order_submission_enabled=bool(snapshot.real_order_submission_enabled),
            payload=payload or {},
        )
        self.append(event)
        return event

    def append(self, event: FinancialEvent) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            line = event.to_json_line()
            with self.log_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
        except FinancialEventLogError:
            raise
        except Exception as exc:
            raise FinancialEventWriteError(
                f"Failed to append financial event to {self.log_path}: {exc}"
            ) from exc

    def read_events(self) -> list[dict[str, Any]]:
        if not self.log_path.exists():
            return []

        events: list[dict[str, Any]] = []
        try:
            with self.log_path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    text = line.strip()
                    if not text:
                        continue
                    event = json.loads(text)
                    validate_event_dict(event)
                    events.append(event)
        except FinancialEventLogError:
            raise
        except Exception as exc:
            raise FinancialEventLogError(
                f"Failed to read financial event log {self.log_path}: {exc}"
            ) from exc
        return events


def env_enabled(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in TRUE_VALUES


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_symbol(symbol: str | None) -> str | None:
    if symbol is None:
        return None
    normalized = str(symbol).strip().upper()
    return normalized or None


def validate_event_dict(event: dict[str, Any]) -> None:
    required = {
        "event_id",
        "timestamp_utc",
        "event_type",
        "correlation_id",
        "symbol",
        "source",
        "runtime_mode",
        "live_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "payload",
    }
    missing = required - set(event)
    if missing:
        raise InvalidFinancialEvent(f"Financial event missing fields: {sorted(missing)}")
    if not isinstance(event["payload"], dict):
        raise InvalidFinancialEvent("Financial event payload must be an object")
    if event["event_type"] not in KNOWN_EVENT_TYPES:
        raise InvalidFinancialEvent(f"Unknown minimum event_type: {event['event_type']}")
    for field_name in (
        "live_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
    ):
        if not isinstance(event[field_name], bool):
            raise InvalidFinancialEvent(f"{field_name} must be boolean")


def record_financial_event(
    event_type: str,
    *,
    payload: dict[str, Any] | None = None,
    symbol: str | None = None,
    correlation_id: str | None = None,
    log_path: str | Path = DEFAULT_LOG_PATH,
    runtime_mode: str = "paper",
    source: str = "smartcrypto",
) -> FinancialEvent:
    logger = FinancialEventLogger(log_path, runtime_mode=runtime_mode, source=source)
    return logger.record(
        event_type,
        payload=payload,
        symbol=symbol,
        correlation_id=correlation_id,
    )
