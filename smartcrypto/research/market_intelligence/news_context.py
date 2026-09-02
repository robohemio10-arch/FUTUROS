"""Read-only bridge from the W2 Research Council snapshot into W3 context."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from smartcrypto.research.research_council import ContextIntelligenceSnapshot


def extract_research_council_context(
    payload: Mapping[str, Any] | None,
    *,
    symbol: str,
    decision_time_utc: datetime,
) -> dict[str, Any] | None:
    if payload is None:
        return None
    snapshot = ContextIntelligenceSnapshot.model_validate(dict(payload))
    if snapshot.symbol != symbol:
        raise ValueError("research_council_symbol_mismatch")
    if snapshot.available_at_utc > decision_time_utc:
        raise ValueError("research_council_context_after_decision_time")
    return {
        "snapshot_id": snapshot.snapshot_id,
        "available_at_utc": snapshot.available_at_utc.isoformat().replace("+00:00", "Z"),
        "valid_until_utc": snapshot.valid_until_utc.isoformat().replace("+00:00", "Z"),
        "context_quality": snapshot.context_quality,
        "consensus_score": snapshot.consensus_score,
        "disagreement_score": snapshot.disagreement_score,
        "uncertainty_score": snapshot.uncertainty_score,
        "news_context": (
            snapshot.news_context.model_dump(mode="json")
            if snapshot.news_context is not None
            else None
        ),
        "macro_context": (
            snapshot.macro_context.model_dump(mode="json")
            if snapshot.macro_context is not None
            else None
        ),
        "regime_context": (
            snapshot.regime_context.model_dump(mode="json")
            if snapshot.regime_context is not None
            else None
        ),
        "research_only": True,
        "operational_authority": False,
    }
