from __future__ import annotations

from typing import Any


__all__ = [
    "RiskLimits",
    "RiskManager",
    "SignalRiskDecision",
    "KillSwitchBlockedError",
    "KillSwitchGuard",
    "KillSwitchResult",
    "evaluate_risk",
    "set_kill_switch",
    "load_kill_switch",
]


def __getattr__(name: str) -> Any:
    if name in {
        "RiskLimits",
        "RiskManager",
        "SignalRiskDecision",
        "evaluate_risk",
        "set_kill_switch",
        "load_kill_switch",
    }:
        from . import risk_manager

        return getattr(risk_manager, name)
    if name in {"KillSwitchBlockedError", "KillSwitchGuard", "KillSwitchResult"}:
        from . import kill_switch_guard

        return getattr(kill_switch_guard, name)
    raise AttributeError(f"module 'smartcrypto.risk' has no attribute {name!r}")
