"""Public research-only Research Council API."""

from .contracts import (
    AgentResult,
    AgentStatus,
    ContextIntelligenceSnapshot,
    CouncilRequest,
    CouncilRunReport,
    MacroAnalysis,
    MarketAnalysis,
    MicrostructureAnalysis,
    NewsAnalysis,
    ProviderAudit,
    ProviderStatus,
    RegimeAnalysis,
    ResearchCouncilConfig,
    StructuredEvidenceInput,
)
from .provider_adapter import (
    DeterministicOfflineProvider,
    ProviderAdapter,
    ProviderExecutionPolicy,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from .service import ResearchCouncilService, load_research_council_config

__all__ = [
    "AgentResult",
    "AgentStatus",
    "ContextIntelligenceSnapshot",
    "CouncilRequest",
    "CouncilRunReport",
    "DeterministicOfflineProvider",
    "MacroAnalysis",
    "MarketAnalysis",
    "MicrostructureAnalysis",
    "NewsAnalysis",
    "ProviderAdapter",
    "ProviderAudit",
    "ProviderExecutionPolicy",
    "ProviderRateLimitError",
    "ProviderStatus",
    "ProviderTimeoutError",
    "RegimeAnalysis",
    "ResearchCouncilConfig",
    "ResearchCouncilService",
    "StructuredEvidenceInput",
    "load_research_council_config",
]

