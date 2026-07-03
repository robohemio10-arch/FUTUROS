"""Research-only, read-only implementation of AI feature missingness remediation.

Derives feature_notional and feature_quantity deterministically from permitted
raw fields on the same dataset row, and proves before/after missingness from a
dataset loaded in memory. Never joins with outcome/feedback/label sources.
"""

from .implementation import build_ai_feature_missingness_remediation_implementation_v1

__all__ = ["build_ai_feature_missingness_remediation_implementation_v1"]
