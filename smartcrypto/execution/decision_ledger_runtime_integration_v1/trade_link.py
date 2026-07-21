"""Read-only trade-link preview and deterministic decision correlation."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from smartcrypto.execution.decision_ledger_runtime_profile_v1 import (
    RuntimeDecisionProjectionV1,
    RuntimeTradeObservationInputV1,
    map_runtime_trade_link,
)

from .contracts import TradeLinkPreviewRequestV1, TradeLinkPreviewResultV1


def build_decision_index(
    projections: Sequence[RuntimeDecisionProjectionV1],
) -> dict[str, RuntimeDecisionProjectionV1]:
    index: dict[str, RuntimeDecisionProjectionV1] = {}
    for projection in projections:
        event_id = projection.target_payload.event_id
        if event_id in index:
            raise ValueError(f"duplicate_decision_event_id:{event_id}")
        index[event_id] = projection
    return index


def preview_trade_link(
    *,
    decision_index: Mapping[str, RuntimeDecisionProjectionV1],
    request: TradeLinkPreviewRequestV1 | Mapping[str, Any],
) -> TradeLinkPreviewResultV1:
    resolved = (
        request
        if isinstance(request, TradeLinkPreviewRequestV1)
        else TradeLinkPreviewRequestV1.model_validate(request)
    )
    decision = decision_index.get(resolved.decision_event_id)
    if decision is None:
        return TradeLinkPreviewResultV1(
            status="blocked",
            reason="decision_event_not_found",
            projection=None,
        )
    observation = RuntimeTradeObservationInputV1.model_validate(
        resolved.trade_observation
    )
    projection = map_runtime_trade_link(decision, observation)
    return TradeLinkPreviewResultV1(
        status="ok",
        reason=None,
        projection=projection,
    )
