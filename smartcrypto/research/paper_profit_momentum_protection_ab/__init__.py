"""Profit-first paper A/B for momentum filters and profit protection."""

from .contracts import (
    RET1_THRESHOLD,
    RET12_THRESHOLD,
    SAFETY_FLAGS,
    SCHEMA_VERSION,
    MomentumProtectionABResult,
)
from .runner import run_momentum_protection_ab
from .simulation import evaluate_momentum_protection_ab

__all__ = [
    "MomentumProtectionABResult",
    "RET1_THRESHOLD",
    "RET12_THRESHOLD",
    "SAFETY_FLAGS",
    "SCHEMA_VERSION",
    "evaluate_momentum_protection_ab",
    "run_momentum_protection_ab",
]
