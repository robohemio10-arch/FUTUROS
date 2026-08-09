"""Path-faithful, walk-forward validation for paper profit protection."""

from .contracts import (
    EXIT_SLIPPAGE_BPS,
    FIXED_PROTECTION_CANDIDATES,
    HOLDOUT_RATIO,
    SAFETY_FLAGS,
    SCHEMA_VERSION,
    PathFaithfulValidationResult,
)
from .runner import run_path_faithful_walkforward

__all__ = [
    "EXIT_SLIPPAGE_BPS",
    "FIXED_PROTECTION_CANDIDATES",
    "HOLDOUT_RATIO",
    "PathFaithfulValidationResult",
    "SAFETY_FLAGS",
    "SCHEMA_VERSION",
    "run_path_faithful_walkforward",
]
