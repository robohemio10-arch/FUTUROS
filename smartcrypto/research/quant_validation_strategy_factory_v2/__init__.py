"""B04 institutional quantitative validation and Strategy Factory V2."""

from .contracts import (
    SAFETY_FLAGS,
    AcceptanceGates,
    CandidateDecision,
    DatasetAuthority,
    RobustnessContract,
    SplitMode,
    StepEvidence,
    StepStatus,
    StrategyCandidate,
    TemporalSplitContract,
    ValidationProtocol,
)
from .factory import DEFAULT_FAMILIES, generate_candidates
from .pipeline import (
    build_quant_validation_strategy_factory_report,
    build_synthetic_candidate_fixture,
    default_config,
)

__all__ = [
    "SAFETY_FLAGS",
    "DEFAULT_FAMILIES",
    "AcceptanceGates",
    "CandidateDecision",
    "DatasetAuthority",
    "RobustnessContract",
    "SplitMode",
    "StepEvidence",
    "StepStatus",
    "StrategyCandidate",
    "TemporalSplitContract",
    "ValidationProtocol",
    "build_quant_validation_strategy_factory_report",
    "build_synthetic_candidate_fixture",
    "default_config",
    "generate_candidates",
]
