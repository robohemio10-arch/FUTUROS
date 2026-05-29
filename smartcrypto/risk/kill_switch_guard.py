from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smartcrypto.state.financial_event_log import KNOWN_EVENT_TYPES, FinancialEventLogger

try:
    import yaml
except Exception:  # pragma: no cover - used only in minimal runtimes without PyYAML.
    yaml = None  # type: ignore[assignment]


TRUE_VALUES = {"1", "true", "yes", "y", "on"}
SAFE_RUNTIME_MODES = {"paper", "research", "shadow"}
# Single source of truth for the kill switch across the whole project.
# Operator (set_kill_switch.py), RiskManager/evaluate_risk, dashboard,
# kill_switch_classifier, kill_switch_guard and preflight all read/write
# this same file. Do NOT introduce a separate kill_switch_guard.json.
DEFAULT_KILL_SWITCH_PATH = "data/runtime/kill_switch.json"
DEFAULT_EVENT_LOG_PATH = "data/runtime/kill_switch_guard_events.jsonl"

CLEAR = "CLEAR"
GLOBAL_BLOCKED = "GLOBAL_BLOCKED"
SYMBOL_BLOCKED = "SYMBOL_BLOCKED"
CORRUPTED = "CORRUPTED"


class KillSwitchGuardError(RuntimeError):
    pass


class KillSwitchSafetyError(KillSwitchGuardError):
    pass


class KillSwitchBlockedError(KillSwitchGuardError):
    def __init__(self, result: "KillSwitchResult") -> None:
        self.result = result
        super().__init__("kill switch blocked operation: " + result.status)


class KillSwitchStateError(KillSwitchGuardError):
    pass


@dataclass(frozen=True)
class KillSwitchResult:
    status: str
    block_operation: bool
    symbol: str | None = None
    reason: str | None = None
    actor: str | None = None
    errors: list[str] = field(default_factory=list)
    checked_at: str = field(default_factory=lambda: utc_timestamp())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class KillSwitchGuard:
    def __init__(
        self,
        *,
        path: str | Path | None = None,
        state_path: str | Path = DEFAULT_KILL_SWITCH_PATH,
        event_logger: FinancialEventLogger | None = None,
        event_log_path: str | Path = DEFAULT_EVENT_LOG_PATH,
        runtime_mode: str = "paper",
    ) -> None:
        assert_runtime_safe(runtime_mode)
        self.state_path = Path(path or state_path)
        self.runtime_mode = str(runtime_mode).strip().lower()
        self.event_logger = event_logger or FinancialEventLogger(
            event_log_path,
            runtime_mode=self.runtime_mode,
            source="kill_switch_guard",
            allowed_event_types=set(KNOWN_EVENT_TYPES),
        )

    @classmethod
    def from_config(cls, path: str | Path) -> "KillSwitchGuard":
        config = load_config(path)
        paths = config.get("paths", {}) if isinstance(config.get("paths"), dict) else {}
        state_path = (
            paths.get("kill_switch_state")
            or paths.get("kill_switch")
            or DEFAULT_KILL_SWITCH_PATH
        )
        return cls(
            state_path=state_path,
            event_log_path=paths.get("financial_event_log", DEFAULT_EVENT_LOG_PATH),
            runtime_mode=str(config.get("runtime_mode", "paper")),
        )

    def check(self, symbol: str | None = None) -> KillSwitchResult:
        return self.evaluate(symbol)

    def is_clear(self, symbol: str | None = None) -> bool:
        return not self.evaluate(symbol).block_operation

    def evaluate(self, symbol: str | None = None) -> KillSwitchResult:
        checked_symbol = normalize_symbol(symbol)
        try:
            state = self.load_state()
        except KillSwitchStateError as exc:
            result = KillSwitchResult(
                status=CORRUPTED,
                block_operation=True,
                symbol=checked_symbol,
                errors=[str(exc)],
            )
            self._record("kill_switch_corrupted", result)
            return result

        global_state = state["global"]
        if global_state["enabled"]:
            result = KillSwitchResult(
                status=GLOBAL_BLOCKED,
                block_operation=True,
                symbol=checked_symbol,
                reason=global_state.get("reason"),
                actor=global_state.get("actor"),
            )
            self._record("kill_switch_blocked", result)
            return result

        if checked_symbol is not None:
            symbol_state = state["symbols"].get(checked_symbol)
            if isinstance(symbol_state, dict) and symbol_state.get("enabled") is True:
                result = KillSwitchResult(
                    status=SYMBOL_BLOCKED,
                    block_operation=True,
                    symbol=checked_symbol,
                    reason=str(symbol_state.get("reason") or ""),
                    actor=str(symbol_state.get("actor") or ""),
                )
                self._record("kill_switch_blocked", result)
                return result

        return KillSwitchResult(status=CLEAR, block_operation=False, symbol=checked_symbol)

    def assert_clear(self, symbol: str | None = None) -> KillSwitchResult:
        result = self.evaluate(symbol)
        if result.block_operation:
            raise KillSwitchBlockedError(result)
        return result

    def activate_global(
        self,
        *,
        reason: str,
        actor: str | None = None,
        operator: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        reason_text = require_text(reason, "reason")
        actor_text = require_actor(actor, operator)
        state = self._load_mutable_state()
        state["global"] = enabled_entry(reason_text, actor_text)
        state["updated_at"] = utc_timestamp()
        self._write_state(state)
        self._record_state_change(
            "kill_switch_triggered",
            state["global"],
            correlation_id=correlation_id,
            scope="global",
        )
        return state

    def clear_global(
        self,
        *,
        actor: str | None = None,
        operator: str | None = None,
        reason: str = "manual_clear",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        actor_text = require_actor(actor, operator)
        reason_text = require_text(reason, "reason")
        state = self._load_mutable_state()
        state["global"] = disabled_entry(reason_text, actor_text)
        state["updated_at"] = utc_timestamp()
        self._write_state(state)
        self._record_state_change(
            "kill_switch_cleared",
            state["global"],
            correlation_id=correlation_id,
            scope="global",
        )
        return state

    def activate_symbol(
        self,
        symbol: str,
        *,
        reason: str,
        actor: str | None = None,
        operator: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        symbol_text = require_symbol(symbol)
        reason_text = require_text(reason, "reason")
        actor_text = require_actor(actor, operator)
        state = self._load_mutable_state()
        state["symbols"][symbol_text] = enabled_entry(reason_text, actor_text)
        state["updated_at"] = utc_timestamp()
        self._write_state(state)
        self._record_state_change(
            "kill_switch_triggered",
            state["symbols"][symbol_text],
            correlation_id=correlation_id,
            scope="symbol",
            symbol=symbol_text,
        )
        return state

    def clear_symbol(
        self,
        symbol: str,
        *,
        actor: str | None = None,
        operator: str | None = None,
        reason: str = "manual_clear",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        symbol_text = require_symbol(symbol)
        actor_text = require_actor(actor, operator)
        reason_text = require_text(reason, "reason")
        state = self._load_mutable_state()
        state["symbols"][symbol_text] = disabled_entry(reason_text, actor_text)
        state["updated_at"] = utc_timestamp()
        self._write_state(state)
        self._record_state_change(
            "kill_switch_cleared",
            state["symbols"][symbol_text],
            correlation_id=correlation_id,
            scope="symbol",
            symbol=symbol_text,
        )
        return state

    def load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return default_state(self.runtime_mode)
        try:
            with self.state_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            raise KillSwitchStateError(f"kill_switch_json_corrupted:{exc}") from exc
        if not isinstance(payload, dict):
            raise KillSwitchStateError("kill_switch_state_root_not_object")
        return normalize_state(payload, self.runtime_mode)

    def _load_mutable_state(self) -> dict[str, Any]:
        return self.load_state()

    def _write_state(self, state: dict[str, Any]) -> None:
        assert_runtime_safe(str(state.get("runtime_mode", self.runtime_mode)))
        normalized = normalize_state(state, self.runtime_mode)
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
            with temp_path.open("w", encoding="utf-8") as handle:
                json.dump(normalized, handle, ensure_ascii=False, indent=2, sort_keys=True)
            temp_path.replace(self.state_path)
        except Exception as exc:
            raise KillSwitchStateError(f"kill_switch_state_write_failed:{exc}") from exc

    def _record(self, event_type: str, result: KillSwitchResult) -> None:
        self.event_logger.record(
            event_type,
            correlation_id=f"kill-switch-{result.checked_at}",
            symbol=result.symbol,
            payload=result.to_dict(),
        )

    def _record_state_change(
        self,
        event_type: str,
        entry: dict[str, Any],
        *,
        correlation_id: str | None,
        scope: str,
        symbol: str | None = None,
    ) -> None:
        self.event_logger.record(
            event_type,
            correlation_id=correlation_id,
            symbol=symbol,
            payload={"scope": scope, "symbol": symbol, **entry},
        )


def default_state(runtime_mode: str = "paper") -> dict[str, Any]:
    now = utc_timestamp()
    return {
        "schema_version": 1,
        "runtime_mode": str(runtime_mode).strip().lower() or "paper",
        "global": disabled_entry("default_clear", "system", timestamp=now),
        "symbols": {},
        "updated_at": now,
    }


def enabled_entry(reason: str, actor: str, timestamp: str | None = None) -> dict[str, Any]:
    return {
        "enabled": True,
        "reason": reason,
        "actor": actor,
        "updated_at": timestamp or utc_timestamp(),
    }


def disabled_entry(reason: str, actor: str, timestamp: str | None = None) -> dict[str, Any]:
    return {
        "enabled": False,
        "reason": reason,
        "actor": actor,
        "updated_at": timestamp or utc_timestamp(),
    }


def normalize_state(payload: dict[str, Any], runtime_mode: str) -> dict[str, Any]:
    state = dict(payload)
    state.setdefault("schema_version", 1)
    state["runtime_mode"] = str(state.get("runtime_mode") or runtime_mode).strip().lower()
    assert_runtime_safe(state["runtime_mode"])
    state.setdefault("updated_at", utc_timestamp())

    # Backward-compatible migration: flat legacy schema written by
    # risk_manager.set_kill_switch -> {"enabled": ..., "reason": ...}
    # is promoted to the structured {"global": {...}} schema. A missing
    # actor is filled with "legacy" so the structured guard never breaks
    # on operator-written files.
    if "global" not in state and "enabled" in state:
        state["global"] = {
            "enabled": bool(state.get("enabled")),
            "reason": str(state.get("reason") or "legacy_state"),
            "actor": str(state.get("actor") or state.get("operator") or "legacy"),
            "updated_at": str(state.get("updated_at") or utc_timestamp()),
        }
    state.setdefault("global", disabled_entry("default_clear", "system"))
    state.setdefault("symbols", {})
    if not isinstance(state["global"], dict):
        raise KillSwitchStateError("kill_switch_global_not_object")
    if not isinstance(state["symbols"], dict):
        raise KillSwitchStateError("kill_switch_symbols_not_object")

    state["global"] = normalize_entry(state["global"], "global")
    state["symbols"] = {
        require_symbol(symbol): normalize_entry(entry, f"symbol:{symbol}")
        for symbol, entry in state["symbols"].items()
    }
    return state


def normalize_entry(entry: dict[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise KillSwitchStateError(f"kill_switch_entry_not_object:{label}")
    enabled = entry.get("enabled")
    if not isinstance(enabled, bool):
        raise KillSwitchStateError(f"kill_switch_enabled_not_boolean:{label}")
    reason = str(entry.get("reason") or "").strip()
    actor = str(entry.get("actor") or entry.get("operator") or "").strip()
    updated_at = str(entry.get("updated_at") or entry.get("timestamp_utc") or "").strip()
    if enabled and not reason:
        raise KillSwitchStateError(f"kill_switch_reason_missing:{label}")
    if enabled and not actor:
        # Operator-written flat files may omit actor; treat as legacy so a
        # genuine global block is never silently lost on normalization.
        actor = "legacy"
    return {
        "enabled": enabled,
        "reason": reason,
        "actor": actor,
        "updated_at": updated_at or utc_timestamp(),
    }


def require_text(value: str, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def require_actor(actor: str | None, operator: str | None) -> str:
    return require_text(actor or operator or "", "actor")


def normalize_symbol(symbol: str | None) -> str | None:
    if symbol is None:
        return None
    normalized = str(symbol).strip().upper()
    return normalized or None


def require_symbol(symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    if normalized is None:
        raise ValueError("symbol is required")
    return normalized


def env_enabled(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in TRUE_VALUES


def assert_runtime_safe(runtime_mode: str) -> None:
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
        raise KillSwitchSafetyError("unsafe kill switch runtime: " + ",".join(reasons))


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
        raise KillSwitchStateError(f"invalid kill switch config root: {target}")
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
            raise KillSwitchStateError("yaml fallback supports simple mappings only")
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