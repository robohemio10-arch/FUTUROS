"""Governed AI Shadow/Qlib research orchestration for B05."""

from .calibration import build_calibration_suite, calibration_report
from .counterfactual import build_counterfactual_harness
from .governance import build_cadence_governance, evaluate_training_eligibility
from .pipeline import build_ai_shadow_qlib_autotrain_v2

__all__ = [
    "build_ai_shadow_qlib_autotrain_v2",
    "build_calibration_suite",
    "build_cadence_governance",
    "build_counterfactual_harness",
    "calibration_report",
    "evaluate_training_eligibility",
]
