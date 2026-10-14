from __future__ import annotations

import json
import hashlib
import os
import re
import stat
import tempfile
import time
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
MAX_STATE_BYTES = 65536
STATE_READ_DEADLINE_SECONDS = 0.75


class KillSwitchGuardError(RuntimeError):
    pass


class KillSwitchSafetyError(KillSwitchGuardError):
    pass


class KillSwitchBlockedError(KillSwitchGuardError):
    def __init__(self, result: "KillSwitchResult") -> None:
        self.result = result
        super().__init__("kill switch blocked operation: " + result.status)


class KillSwitchStateError(KillSwitchGuardError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


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


@dataclass(frozen=True)
class KillSwitchSnapshot:
    payload: dict[str, Any]
    state: dict[str, Any]
    sha256: str
    source_format: str

    def result(self, symbol: str | None = None) -> KillSwitchResult:
        if symbol is not None and (not isinstance(symbol, str) or not symbol.strip()):
            raise KillSwitchStateError("kill_switch_symbol_identity_invalid")
        checked_symbol = normalize_symbol(symbol)
        if checked_symbol is not None:
            validate_symbol_identity(checked_symbol)
        global_state = self.state["global"]
        if global_state["enabled"]:
            return KillSwitchResult(
                GLOBAL_BLOCKED,
                True,
                checked_symbol,
                global_state.get("reason"),
                global_state.get("actor"),
            )
        symbol_state = self.state["symbols"].get(checked_symbol)
        if symbol_state is not None and symbol_state["enabled"]:
            return KillSwitchResult(
                SYMBOL_BLOCKED,
                True,
                checked_symbol,
                symbol_state["reason"],
                symbol_state["actor"],
            )
        if checked_symbol is None and any(
            entry["enabled"] for entry in self.state["symbols"].values()
        ):
            return KillSwitchResult(SYMBOL_BLOCKED, True, reason="symbol_scope_required")
        return KillSwitchResult(CLEAR, False, checked_symbol)


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
            paths.get("kill_switch_state") or paths.get("kill_switch") or DEFAULT_KILL_SWITCH_PATH
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
        result = self.inspect(symbol)
        if result.status == CORRUPTED:
            self._record("kill_switch_corrupted", result)
        elif result.block_operation:
            self._record("kill_switch_blocked", result)
        return result

    def inspect(self, symbol: str | None = None) -> KillSwitchResult:
        """Safety decision without event logging, state creation, or migration."""
        return inspect_kill_switch(self.state_path, symbol, self.runtime_mode)

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
        state = update_global_entry(state, enabled_entry(reason_text, actor_text))
        state["updated_at"] = utc_timestamp()
        self._write_state(state)
        self._record_state_change(
            "kill_switch_triggered",
            global_entry(state),
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
        state = update_global_entry(state, disabled_entry(reason_text, actor_text))
        state["updated_at"] = utc_timestamp()
        self._write_state(state)
        self._record_state_change(
            "kill_switch_cleared",
            global_entry(state),
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
        if "schema_version" not in state:
            raise KillSwitchStateError("kill_switch_explicit_migration_required_for_symbol_write")
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
        if "schema_version" not in state:
            raise KillSwitchStateError("kill_switch_explicit_migration_required_for_symbol_write")
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
        return read_kill_switch_snapshot(self.state_path, self.runtime_mode).state

    def _load_mutable_state(self) -> dict[str, Any]:
        return load_operator_state(self.state_path, self.runtime_mode)

    def _write_state(self, state: dict[str, Any]) -> None:
        write_operator_state(self.state_path, state, self.runtime_mode)

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
    """Interpret explicit evidence only; never synthesize authorization metadata."""
    if payload.get("runtime_mode") != runtime_mode or runtime_mode not in SAFE_RUNTIME_MODES:
        raise KillSwitchStateError("kill_switch_explicit_schema_v1_paper_required")
    structured = any(key in payload for key in ("schema_version", "global", "symbols"))
    if not structured:
        if type(payload.get("enabled")) is not bool:
            raise KillSwitchStateError("kill_switch_explicit_schema_v1_paper_required")
        if not payload["enabled"]:
            raise KillSwitchStateError("kill_switch_legacy_clear_not_authorization")
        # An explicit legacy halt is sufficient to deny, never to authorize.
        for key in ("reason", "actor", "operator"):
            if key in payload and (not isinstance(payload[key], str) or not payload[key].strip()):
                raise KillSwitchStateError("kill_switch_explicit_entry_metadata_required")
        if "actor" in payload and "operator" in payload and payload["actor"] != payload["operator"]:
            raise KillSwitchStateError("kill_switch_conflicting_entry_actor")
        updated = validate_utc_timestamp(payload["updated_at"]) if "updated_at" in payload else None
        return {
            "runtime_mode": runtime_mode,
            "global": {
                "enabled": True,
                "reason": payload.get("reason"),
                "actor": payload.get("actor") or payload.get("operator"),
                "updated_at": updated,
            },
            "symbols": {},
            "updated_at": updated,
        }
    if (
        type(payload.get("schema_version")) is not int
        or payload["schema_version"] != 1
        or "enabled" in payload
        or not isinstance(payload.get("symbols"), dict)
    ):
        raise KillSwitchStateError("kill_switch_explicit_schema_v1_paper_required")
    updated = validate_utc_timestamp(payload.get("updated_at"))
    normalized_symbols: dict[str, dict[str, Any]] = {}
    names: set[str] = set()
    for symbol, entry in payload["symbols"].items():
        normalized = normalize_symbol(symbol)
        if normalized in names:
            raise KillSwitchStateError("kill_switch_symbol_identity_collision")
        names.add(str(normalized))
    for symbol, entry in payload["symbols"].items():
        validate_symbol_identity(symbol)
        normalized_symbols[symbol] = normalize_entry(entry, f"symbol:{symbol}")
    global_state = normalize_entry(payload.get("global"), "global")
    for entry in (global_state, *normalized_symbols.values()):
        if datetime.fromisoformat(entry["updated_at"]) > datetime.fromisoformat(updated):
            raise KillSwitchStateError("kill_switch_entry_newer_than_state")
    return {
        **payload,
        "global": global_state,
        "symbols": normalized_symbols,
        "updated_at": updated,
    }


def normalize_entry(entry: object, label: str) -> dict[str, Any]:
    if not isinstance(entry, dict) or type(entry.get("enabled")) is not bool:
        raise KillSwitchStateError("kill_switch_explicit_boolean_entry_required")
    reason = entry.get("reason")
    actor = entry.get("actor") or entry.get("operator")
    if (
        not isinstance(reason, str)
        or not reason.strip()
        or not isinstance(actor, str)
        or not actor.strip()
    ):
        raise KillSwitchStateError("kill_switch_explicit_entry_metadata_required")
    if reason.strip().lower() == "default_clear":
        raise KillSwitchStateError("kill_switch_default_clear_not_authorization")
    updated_at = validate_utc_timestamp(entry.get("updated_at") or entry.get("timestamp_utc"))
    if "updated_at" in entry and "timestamp_utc" in entry:
        if validate_utc_timestamp(entry["updated_at"]) != validate_utc_timestamp(
            entry["timestamp_utc"]
        ):
            raise KillSwitchStateError("kill_switch_conflicting_entry_timestamps")
    if "actor" in entry and "operator" in entry and entry["actor"] != entry["operator"]:
        raise KillSwitchStateError("kill_switch_conflicting_entry_actor")
    return {
        "enabled": entry["enabled"],
        "reason": reason,
        "actor": actor,
        "updated_at": updated_at,
    }


def validate_utc_timestamp(value: object) -> str:
    if not isinstance(value, str):
        raise KillSwitchStateError("kill_switch_explicit_utc_timestamp_required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise KillSwitchStateError("kill_switch_explicit_utc_timestamp_required") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise KillSwitchStateError("kill_switch_explicit_utc_timestamp_required")
    if parsed > datetime.now(timezone.utc):
        raise KillSwitchStateError("kill_switch_timestamp_in_future")
    return parsed.isoformat()


def validate_symbol_identity(symbol: object) -> None:
    if (
        not isinstance(symbol, str)
        or re.fullmatch(r"[A-Z0-9]+(?:/[A-Z0-9]+(?::[A-Z0-9]+)?)?", symbol) is None
    ):
        raise KillSwitchStateError("kill_switch_symbol_identity_invalid")


def assert_local_regular_path(
    path: Path, *, directory: bool = False, allow_missing: bool = False
) -> None:
    if str(path.absolute()).startswith(("\\\\", "//")):
        raise KillSwitchStateError("kill_switch_local_runtime_root_required")
    for part in (path.absolute(), *path.absolute().parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            if allow_missing:
                continue
            raise
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024
        ):
            raise KillSwitchStateError("kill_switch_reparse_or_symlink_forbidden")
    if path.exists() or not allow_missing:
        mode = path.stat().st_mode
        if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
            raise KillSwitchStateError("kill_switch_regular_local_path_required")


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise KillSwitchStateError("kill_switch_duplicate_json_key")
        result[key] = value
    return result


def _invalid_json_constant(value: str) -> None:
    raise KillSwitchStateError("kill_switch_invalid_json_constant")


def read_kill_switch_snapshot(
    path: str | Path, runtime_mode: str = "paper", *, operator: bool = False
) -> KillSwitchSnapshot:
    target = Path(path)
    started = time.monotonic()
    try:
        assert_local_regular_path(target)
        before = target.stat()
        if before.st_size > MAX_STATE_BYTES:
            raise KillSwitchStateError("kill_switch_state_size_limit")
        with target.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            raw = handle.read(MAX_STATE_BYTES + 1)
            after_handle = os.fstat(handle.fileno())
        assert_local_regular_path(target)
        after = target.stat()
        signatures = {
            (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns)
            for row in (before, opened, after_handle, after)
        }
        if (
            len(signatures) != 1
            or len(raw) != before.st_size
            or before.st_ctime_ns != after.st_ctime_ns
            or opened.st_ctime_ns != after_handle.st_ctime_ns
        ):
            raise KillSwitchStateError("kill_switch_changed_during_read")
        if time.monotonic() - started >= STATE_READ_DEADLINE_SECONDS:
            raise KillSwitchStateError("kill_switch_read_deadline")
        try:
            payload = json.loads(
                raw, object_pairs_hook=_json_object, parse_constant=_invalid_json_constant
            )
        except (ValueError, UnicodeError) as exc:
            raise KillSwitchStateError("kill_switch_json_corrupted") from exc
        if not isinstance(payload, dict):
            raise KillSwitchStateError("kill_switch_state_root_not_object")
        # Operator reads may preserve an explicit legacy CLEAR for a manual write;
        # that state still cannot authorize any safety reader.
        interpreted = dict(payload)
        if (
            operator
            and not any(key in payload for key in ("schema_version", "global", "symbols"))
            and payload.get("enabled") is False
        ):
            interpreted["enabled"] = True
        state = normalize_state(interpreted, runtime_mode)
        snapshot = KillSwitchSnapshot(
            payload,
            state,
            hashlib.sha256(raw).hexdigest(),
            "structured_v1" if "schema_version" in payload else "legacy",
        )
        if time.monotonic() - started >= STATE_READ_DEADLINE_SECONDS:
            raise KillSwitchStateError("kill_switch_read_deadline")
        return snapshot
    except KillSwitchStateError:
        raise
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        raise KillSwitchStateError(f"kill_switch_read_{type(exc).__name__}") from exc


def inspect_kill_switch(
    path: str | Path, symbol: str | None = None, runtime_mode: str = "paper"
) -> KillSwitchResult:
    try:
        return read_kill_switch_snapshot(path, runtime_mode).result(symbol)
    except KillSwitchStateError as exc:
        return KillSwitchResult(
            CORRUPTED, True, normalize_symbol(symbol), reason=exc.reason, errors=[exc.reason]
        )


def load_operator_state(
    path: Path, runtime_mode: str, *, legacy_initialization: bool = False
) -> dict[str, Any]:
    try:
        return read_kill_switch_snapshot(path, runtime_mode, operator=True).payload
    except KillSwitchStateError as exc:
        if exc.reason != "kill_switch_read_FileNotFoundError":
            raise
        assert_local_regular_path(path, allow_missing=True)
        # Initialization is only reached by an explicit operator mutation.
        entry = enabled_entry("operator_initialization_blocked", "operator")
        if legacy_initialization:
            return {"runtime_mode": runtime_mode, **entry}
        return {
            "schema_version": 1,
            "runtime_mode": runtime_mode,
            "global": entry,
            "symbols": {},
            "updated_at": entry["updated_at"],
        }


def global_entry(state: dict[str, Any]) -> dict[str, Any]:
    return (
        state["global"]
        if "schema_version" in state
        else {key: state.get(key) for key in ("enabled", "reason", "actor", "updated_at")}
    )


def update_global_entry(state: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    if "schema_version" in state:
        return {**state, "global": entry}
    return {**{key: value for key, value in state.items() if key != "operator"}, **entry}


def write_operator_state(path: Path, state: dict[str, Any], runtime_mode: str = "paper") -> None:
    assert_runtime_safe(runtime_mode)
    interpreted = dict(state)
    if "schema_version" not in state and state.get("enabled") is False:
        interpreted["enabled"] = True
    normalize_state(interpreted, runtime_mode)
    temporary: Path | None = None
    try:
        assert_local_regular_path(path, allow_missing=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        assert_local_regular_path(path.parent, directory=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        assert_local_regular_path(path, allow_missing=True)
        temporary.replace(path)
        temporary = None
    except (OSError, ValueError) as exc:
        raise KillSwitchStateError(f"kill_switch_state_write_failed:{type(exc).__name__}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


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
