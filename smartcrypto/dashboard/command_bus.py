from __future__ import annotations

import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from smartcrypto.risk.kill_switch_guard import KillSwitchGuard
from smartcrypto.state.financial_event_log import (
    DASHBOARD_COMMAND_EVENT_TYPES,
    KNOWN_EVENT_TYPES,
    FinancialEventLogger,
    utc_timestamp,
)


TRUE_VALUES = {"1", "true", "yes", "y", "on"}
SAFE_RUNTIME_MODES = {"paper", "research", "shadow"}
ACCEPTED = "ACCEPTED"
REJECTED = "REJECTED"
READONLY_BLOCKED = "READONLY_BLOCKED"
ALLOWED_COMMANDS = {
    "refresh_metrics",
    "request_paper_snapshot",
    "request_reconciliation_check",
    "request_market_health_check",
}
PROHIBITED_COMMANDS = {
    "submit_order",
    "cancel_order",
    "force_close",
    "enable_live",
    "disable_kill_switch",
    "change_risk_limits",
}


class DashboardCommandBusError(RuntimeError):
    pass


class DashboardCommandValidationError(DashboardCommandBusError):
    pass


@dataclass(frozen=True)
class DashboardCommand:
    command_id: str
    command: str
    correlation_id: str
    operator: str
    source: str
    payload: dict[str, Any]
    status: str
    reasons: list[str]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DashboardCommandBusConfig:
    runtime_mode: str = "paper"
    readonly: bool = True
    event_log_path: str = "data/runtime/dashboard_command_bus_events.jsonl"
    allowed_commands: set[str] = field(default_factory=lambda: set(ALLOWED_COMMANDS))
    prohibited_commands: set[str] = field(default_factory=lambda: set(PROHIBITED_COMMANDS))


class DashboardReadonlyCommandBus:
    def __init__(
        self,
        *,
        runtime_mode: str = "paper",
        readonly: bool = True,
        event_logger: FinancialEventLogger | None = None,
        event_log_path: str | Path = "data/runtime/dashboard_command_bus_events.jsonl",
        kill_switch_path: str | Path = "data/runtime/kill_switch_guard.json",
        allowed_commands: set[str] | None = None,
        prohibited_commands: set[str] | None = None,
    ) -> None:
        self.runtime_mode = str(runtime_mode).strip().lower()
        self.readonly = bool(readonly)
        self.allowed_commands = set(allowed_commands or ALLOWED_COMMANDS)
        self.prohibited_commands = set(prohibited_commands or PROHIBITED_COMMANDS)
        self.event_logger = event_logger or FinancialEventLogger(
            event_log_path,
            runtime_mode=self.runtime_mode,
            source="dashboard_command_bus",
            allowed_event_types=set(KNOWN_EVENT_TYPES) | DASHBOARD_COMMAND_EVENT_TYPES,
        )
        self.kill_switch_guard = KillSwitchGuard(
            state_path=kill_switch_path,
            event_logger=self.event_logger,
            runtime_mode=self.runtime_mode,
        )

    def submit(
        self,
        command: str,
        *,
        correlation_id: str,
        operator: str,
        source: str,
        payload: dict[str, Any] | None = None,
    ) -> DashboardCommand:
        command_name = normalize_command(command)
        clean_correlation_id = require_text(correlation_id, "correlation_id")
        clean_operator = require_text(operator, "operator")
        clean_source = require_text(source, "source")
        command_id = str(uuid.uuid4())
        safe_payload = dict(payload or {})
        base_payload = {
            "command_id": command_id,
            "command": command_name,
            "operator": clean_operator,
            "source": clean_source,
            "payload": safe_payload,
        }
        reasons = self._rejection_reasons(command_name)
        if not has_runtime_guard_reason(reasons):
            kill_switch_result = self.kill_switch_guard.evaluate(
                extract_symbol_from_payload(safe_payload)
            )
            if kill_switch_result.block_operation:
                reasons.append(f"kill_switch_{kill_switch_result.status.lower()}")
        if reasons:
            status = (
                READONLY_BLOCKED
                if "dashboard_readonly" in reasons and not has_runtime_guard_reason(reasons)
                else REJECTED
            )
            result = DashboardCommand(
                command_id=command_id,
                command=command_name,
                correlation_id=clean_correlation_id,
                operator=clean_operator,
                source=clean_source,
                payload=safe_payload,
                status=status,
                reasons=reasons,
                created_at=utc_timestamp(),
            )
            self._record_rejection(result)
            return result

        self.event_logger.record(
            "dashboard_command_received",
            correlation_id=clean_correlation_id,
            source=clean_source,
            payload=base_payload,
        )

        result = DashboardCommand(
            command_id=command_id,
            command=command_name,
            correlation_id=clean_correlation_id,
            operator=clean_operator,
            source=clean_source,
            payload=safe_payload,
            status=ACCEPTED,
            reasons=[],
            created_at=utc_timestamp(),
        )
        self.event_logger.record(
            "dashboard_command_accepted",
            correlation_id=clean_correlation_id,
            source=clean_source,
            payload=result.to_dict(),
        )
        return result

    def _rejection_reasons(self, command: str) -> list[str]:
        reasons: list[str] = []
        if self.runtime_mode not in SAFE_RUNTIME_MODES:
            reasons.append(f"runtime_mode_not_allowed:{self.runtime_mode}")
        if env_enabled("LIVE_ENABLED"):
            reasons.append("LIVE_ENABLED=true")
        if env_enabled("ORDER_SUBMISSION_ENABLED"):
            reasons.append("ORDER_SUBMISSION_ENABLED=true")
        if env_enabled("REAL_ORDER_SUBMISSION_ENABLED"):
            reasons.append("REAL_ORDER_SUBMISSION_ENABLED=true")
        if command in self.prohibited_commands:
            reasons.append(f"prohibited_command:{command}")
        if command not in self.allowed_commands:
            reasons.append(f"command_not_allowed:{command}")
        if self.readonly and command in self.prohibited_commands:
            reasons.append("dashboard_readonly")
        return reasons

    def _record_rejection(self, command: DashboardCommand) -> None:
        event_type = rejection_event_type(command)
        self.event_logger.record(
            event_type,
            correlation_id=command.correlation_id,
            source=command.source,
            payload=command.to_dict(),
        )


def env_enabled(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in TRUE_VALUES


def has_runtime_guard_reason(reasons: list[str]) -> bool:
    runtime_reasons = {
        "LIVE_ENABLED=true",
        "ORDER_SUBMISSION_ENABLED=true",
        "REAL_ORDER_SUBMISSION_ENABLED=true",
    }
    return any(
        reason in runtime_reasons
        or reason.startswith("runtime_mode_not_allowed:")
        for reason in reasons
    )


def rejection_event_type(command: DashboardCommand) -> str:
    if has_runtime_guard_reason(command.reasons):
        return "runtime_guard_blocked"
    if command.status == READONLY_BLOCKED:
        return "dashboard_readonly_blocked"
    return "dashboard_command_rejected"


def extract_symbol_from_payload(payload: dict[str, Any]) -> str | None:
    value = payload.get("symbol") or payload.get("pair")
    text = str(value or "").strip().upper()
    return text or None


def normalize_command(command: str) -> str:
    value = require_text(command, "command").strip().lower()
    return value


def require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise DashboardCommandValidationError(f"{field_name} is required")
    return text
