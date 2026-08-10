"""Frozen paper momentum forward OOS observer."""

from __future__ import annotations

from typing import Any

from .contracts import MomentumForwardOOSResult


def run_momentum_forward_oos_observer(
    *args: Any,
    **kwargs: Any,
) -> MomentumForwardOOSResult:
    """Import the runner lazily to keep package import lightweight."""
    from .runner import run_momentum_forward_oos_observer as implementation

    return implementation(*args, **kwargs)


__all__ = [
    "MomentumForwardOOSResult",
    "run_momentum_forward_oos_observer",
]
