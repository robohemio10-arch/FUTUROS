from __future__ import annotations

from typing import Any


__all__ = ["evaluate_risk", "set_kill_switch", "load_kill_switch"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from . import risk_manager

        return getattr(risk_manager, name)
    raise AttributeError(f"module 'smartcrypto.risk' has no attribute {name!r}")
