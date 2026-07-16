"""Guarded one-shot transition for a future authorized legacy OCR append."""

from .contract import (
    DEFAULT_TRANSITION_CONTRACT,
    TransitionContract,
    TransitionContractError,
    load_transition_contract,
)
from .executor import apply_authorized_append, verify_authorized_append
from .planner import build_authorized_append_plan

__all__ = [
    "DEFAULT_TRANSITION_CONTRACT",
    "TransitionContract",
    "TransitionContractError",
    "apply_authorized_append",
    "build_authorized_append_plan",
    "load_transition_contract",
    "verify_authorized_append",
]
