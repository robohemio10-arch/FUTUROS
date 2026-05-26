from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


TRUE_VALUES = {"1", "true", "yes", "y", "on"}
SAFE_RUNTIME_MODES = {"paper", "research", "shadow"}
DEFAULT_STATE_PATH = "data/runtime/state_repository.json"


class StateRepositoryError(RuntimeError):
    pass


class StateSafetyError(StateRepositoryError):
    pass


class StatePersistenceError(StateRepositoryError):
    pass


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
        raise StateSafetyError("unsafe state repository runtime: " + ",".join(reasons))


def default_state(runtime_mode: str = "paper", max_capital_global: float = 0.0) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "runtime_mode": runtime_mode,
        "created_at": utc_timestamp(),
        "updated_at": utc_timestamp(),
        "capital": {
            "max_capital_global": float(max_capital_global),
            "reserved_notional": 0.0,
            "filled_notional": 0.0,
            "available_notional": float(max_capital_global),
        },
        "reservations": {},
        "positions": {},
        "events": [],
    }


class StateRepository:
    def __init__(
        self,
        path: str | Path = DEFAULT_STATE_PATH,
        *,
        runtime_mode: str = "paper",
        max_capital_global: float = 0.0,
    ) -> None:
        assert_runtime_safe(runtime_mode)
        self.path = Path(path)
        self.runtime_mode = str(runtime_mode).strip().lower()
        self.max_capital_global = float(max_capital_global)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            state = default_state(self.runtime_mode, self.max_capital_global)
            self.save(state)
            return state

        try:
            with self.path.open("r", encoding="utf-8") as handle:
                state = json.load(handle)
        except Exception as exc:
            raise StatePersistenceError(
                f"failed to read state repository {self.path}: {exc}"
            ) from exc

        if not isinstance(state, dict):
            raise StatePersistenceError("state repository root must be a JSON object")
        return self._normalize_state(state)

    def save(self, state: dict[str, Any]) -> None:
        assert_runtime_safe(str(state.get("runtime_mode", self.runtime_mode)))
        normalized = self._normalize_state(state)
        normalized["updated_at"] = utc_timestamp()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
            with temp_path.open("w", encoding="utf-8") as handle:
                json.dump(normalized, handle, ensure_ascii=False, indent=2, sort_keys=True)
            temp_path.replace(self.path)
        except Exception as exc:
            raise StatePersistenceError(
                f"failed to write state repository {self.path}: {exc}"
            ) from exc

    def update(self, mutator: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
        state = self.load()
        working_state = deepcopy(state)
        mutator(working_state)
        self.save(working_state)
        return working_state

    def append_event(self, event: dict[str, Any]) -> dict[str, Any]:
        def mutate(state: dict[str, Any]) -> None:
            state.setdefault("events", []).append(event)

        return self.update(mutate)

    def _normalize_state(self, state: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(state)
        normalized.setdefault("schema_version", 1)
        normalized.setdefault("runtime_mode", self.runtime_mode)
        assert_runtime_safe(str(normalized["runtime_mode"]))
        normalized.setdefault("created_at", utc_timestamp())
        normalized.setdefault("updated_at", utc_timestamp())
        normalized.setdefault("reservations", {})
        normalized.setdefault("positions", {})
        normalized.setdefault("events", [])
        normalized.setdefault("capital", {})
        capital = normalized["capital"]
        if not isinstance(capital, dict):
            raise StatePersistenceError("state capital must be an object")
        capital.setdefault("max_capital_global", self.max_capital_global)
        capital["max_capital_global"] = float(capital["max_capital_global"])
        recompute_capital(normalized)
        return normalized


def recompute_capital(state: dict[str, Any]) -> None:
    reservations = state.get("reservations", {})
    if not isinstance(reservations, dict):
        raise StatePersistenceError("state reservations must be an object")
    capital = state.setdefault("capital", {})
    max_capital = float(capital.get("max_capital_global", 0.0))
    reserved = 0.0
    filled = 0.0
    for reservation in reservations.values():
        if not isinstance(reservation, dict):
            continue
        status = str(reservation.get("status", "")).upper()
        notional = float(reservation.get("notional", 0.0))
        if status == "RESERVED":
            reserved += notional
        elif status == "FILLED":
            filled += notional
    capital["reserved_notional"] = float(reserved)
    capital["filled_notional"] = float(filled)
    capital["available_notional"] = float(max_capital - reserved - filled)
