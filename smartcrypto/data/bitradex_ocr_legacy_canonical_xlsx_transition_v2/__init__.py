"""Guarded canonical-XLSX transition for the legacy Bitradex OCR Master."""

from .contract import (
    DEFAULT_TRANSITION_CONTRACT,
    TransitionContract,
    TransitionContractError,
    load_transition_contract,
)
from .executor import (
    apply_canonical_xlsx_transition,
    verify_canonical_xlsx_transition,
)
from .planner import build_canonical_xlsx_transition_plan

__all__ = [
    "DEFAULT_TRANSITION_CONTRACT",
    "TransitionContract",
    "TransitionContractError",
    "apply_canonical_xlsx_transition",
    "build_canonical_xlsx_transition_plan",
    "load_transition_contract",
    "verify_canonical_xlsx_transition",
]
