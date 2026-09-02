"""Research-only W4 regime router and ensemble abstention package."""

from .contracts import (
    AIShadowDecision,
    AIShadowVetoEvidence,
    AibotParityResearchConfig,
    DirectionalEvidencePoint,
    EnsembleAbstentionConfig,
    EnsembleAbstentionDecision,
    EnsembleAbstentionRequest,
    EnsembleRunReport,
    EnsembleStatus,
    QlibDirectionalEvidence,
    RegimeAlignment,
    RegimeLabel,
    RegimeRoute,
    RegimeRouteStatus,
    ResearchAction,
)
from .ensemble import evaluate_ensemble_abstention
from .regime_router import build_regime_route, normalize_regime_label
from .service import load_aibot_parity_config, run_ensemble_abstention

__all__ = [
    "AIShadowDecision",
    "AIShadowVetoEvidence",
    "AibotParityResearchConfig",
    "DirectionalEvidencePoint",
    "EnsembleAbstentionConfig",
    "EnsembleAbstentionDecision",
    "EnsembleAbstentionRequest",
    "EnsembleRunReport",
    "EnsembleStatus",
    "QlibDirectionalEvidence",
    "RegimeAlignment",
    "RegimeLabel",
    "RegimeRoute",
    "RegimeRouteStatus",
    "ResearchAction",
    "build_regime_route",
    "evaluate_ensemble_abstention",
    "load_aibot_parity_config",
    "normalize_regime_label",
    "run_ensemble_abstention",
]
