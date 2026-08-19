"""Research-only Shadow Opportunity Engine V1."""

from .contracts import CandidateObservation, MarketEvidence, PositionSnapshot
from .engine import (
    SAFETY_FLAGS,
    ShadowOpportunityEngine,
    build_candidate,
    build_shadow_opportunity_engine_v1,
)

__all__ = [
    "CandidateObservation",
    "MarketEvidence",
    "PositionSnapshot",
    "SAFETY_FLAGS",
    "ShadowOpportunityEngine",
    "build_candidate",
    "build_shadow_opportunity_engine_v1",
]
