"""Prospective, research-only AIBOT Parity Paper A/B + soak evaluation."""

from .evaluator import (
    Preregistration,
    build_preregistration,
    evaluate_prospective_ab_soak,
    load_preregistration,
)

__all__ = [
    "Preregistration",
    "build_preregistration",
    "evaluate_prospective_ab_soak",
    "load_preregistration",
]
