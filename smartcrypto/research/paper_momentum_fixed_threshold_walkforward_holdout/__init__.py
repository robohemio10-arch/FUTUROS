"""Fixed-threshold momentum walk-forward/holdout research package."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .contracts import MomentumFixedThresholdValidationResult


def run_fixed_threshold_momentum_validation(
    *args: Any,
    **kwargs: Any,
) -> MomentumFixedThresholdValidationResult:
    """Load the runner lazily and execute fixed-threshold validation."""
    from .runner import run_fixed_threshold_momentum_validation as implementation

    runner: Callable[..., MomentumFixedThresholdValidationResult] = implementation
    return runner(*args, **kwargs)


__all__ = [
    "MomentumFixedThresholdValidationResult",
    "run_fixed_threshold_momentum_validation",
]
