"""W8 Execution Intelligence research-only public API."""

from .contracts import (
    CandleGranularity,
    ExecutionCostModel,
    ExecutionEvaluation,
    ExecutionIntelligenceRequest,
    ExecutionIntelligenceSnapshot,
    ExecutionPolicyName,
    ExecutionScenario,
    ExecutionStatus,
    ExitPolicyEvaluation,
    ExitPolicyName,
    ExitStatus,
    IntrabarBar,
    IntrabarExitScenario,
    LiquidityRole,
    MarketSlice,
    PositionSide,
    SafetyContract,
    Side,
)
from .intrabar_exit_lab import evaluate_intrabar_scenario
from .simulator import build_snapshot

__all__ = [
    "CandleGranularity",
    "ExecutionCostModel",
    "ExecutionEvaluation",
    "ExecutionIntelligenceRequest",
    "ExecutionIntelligenceSnapshot",
    "ExecutionPolicyName",
    "ExecutionScenario",
    "ExecutionStatus",
    "ExitPolicyEvaluation",
    "ExitPolicyName",
    "ExitStatus",
    "IntrabarBar",
    "IntrabarExitScenario",
    "LiquidityRole",
    "MarketSlice",
    "PositionSide",
    "SafetyContract",
    "Side",
    "build_snapshot",
    "evaluate_intrabar_scenario",
]
