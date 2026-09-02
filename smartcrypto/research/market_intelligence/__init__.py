"""Public research-only Market Intelligence API."""

from .ablation import build_ablation_manifest
from .contracts import (
    AblationManifest,
    AblationVariant,
    FeatureDefinition,
    FeatureFamilyHealth,
    FreshnessStatus,
    MarketEvent,
    MarketIntelligenceConfig,
    MarketIntelligenceRequest,
    MarketIntelligenceRunReport,
    MarketIntelligenceSnapshot,
    MarketIntelligenceStatus,
    SourceWatermark,
)
from .snapshot import (
    MarketIntelligenceService,
    build_source_watermarks,
    load_market_intelligence_config,
)

__all__ = [
    "AblationManifest",
    "AblationVariant",
    "FeatureDefinition",
    "FeatureFamilyHealth",
    "FreshnessStatus",
    "MarketEvent",
    "MarketIntelligenceConfig",
    "MarketIntelligenceRequest",
    "MarketIntelligenceRunReport",
    "MarketIntelligenceService",
    "MarketIntelligenceSnapshot",
    "MarketIntelligenceStatus",
    "SourceWatermark",
    "build_ablation_manifest",
    "build_source_watermarks",
    "load_market_intelligence_config",
]
