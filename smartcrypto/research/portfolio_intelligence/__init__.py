"""W5 Opportunity Book V2 and shadow Portfolio Allocator."""

from .alpha_registry import build_alpha_registry, registered_strategy_ids
from .allocator import allocate_shadow_portfolio
from .contracts import (
    AllocationAction,
    AllocationDecision,
    AlphaDefinition,
    AlphaRegistrySnapshot,
    CandidateEVEstimate,
    CandidateOpportunity,
    CorrelationObservation,
    OpenPositionOpportunity,
    OpportunityBookRequest,
    OpportunityBookSnapshot,
    PortfolioAllocationSnapshot,
    PortfolioAllocatorConfig,
    PortfolioAllocatorRequest,
    RemainingEVEstimate,
    ReplacementEvaluation,
    ReplacementInput,
    ResearchAction,
    RiskPenaltyEstimate,
    TransitionCostEstimate,
)
from .opportunity_book import build_opportunity_book
from .replacement_policy import evaluate_replacement

__all__ = [
    "AllocationAction",
    "AllocationDecision",
    "AlphaDefinition",
    "AlphaRegistrySnapshot",
    "CandidateEVEstimate",
    "CandidateOpportunity",
    "CorrelationObservation",
    "OpenPositionOpportunity",
    "OpportunityBookRequest",
    "OpportunityBookSnapshot",
    "PortfolioAllocationSnapshot",
    "PortfolioAllocatorConfig",
    "PortfolioAllocatorRequest",
    "RemainingEVEstimate",
    "ReplacementEvaluation",
    "ReplacementInput",
    "ResearchAction",
    "RiskPenaltyEstimate",
    "TransitionCostEstimate",
    "allocate_shadow_portfolio",
    "build_alpha_registry",
    "build_opportunity_book",
    "evaluate_replacement",
    "registered_strategy_ids",
]
