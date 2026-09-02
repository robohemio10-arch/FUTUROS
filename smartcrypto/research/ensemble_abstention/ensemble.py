"""Deterministic research-only ensemble disagreement and ABSTAIN policy."""

from __future__ import annotations

import math
from datetime import timedelta
from statistics import fmean

from .contracts import (
    AIShadowDecision,
    DirectionalEvidencePoint,
    EnsembleAbstentionConfig,
    EnsembleAbstentionDecision,
    EnsembleAbstentionRequest,
    EnsembleStatus,
    RegimeAlignment,
    RegimeLabel,
    RegimeRouteStatus,
    ResearchAction,
    canonical_sha256,
)
from .regime_router import build_regime_route

# Qlib, Research Council and causal Market Intelligence are independent directional
# inputs. The Regime Router is intentionally NOT counted as another directional vote:
# it is derived from upstream evidence and is used only for alignment/risk context.
_EXPECTED_DIRECTIONAL_SOURCES = 3


def evaluate_ensemble_abstention(
    request: EnsembleAbstentionRequest,
    config: EnsembleAbstentionConfig,
) -> EnsembleAbstentionDecision:
    route = build_regime_route(
        request,
        default_regime_confidence_when_missing=float(
            config.default_regime_confidence_when_missing
        ),
    )
    proposed_side = request.qlib.proposed_side if request.qlib is not None else "no_trade"
    alignment = _alignment(proposed_side, route.regime_label)

    pit_reasons: list[str] = []
    if route.status is RegimeRouteStatus.INVALID_POINT_IN_TIME:
        pit_reasons.append(route.reason)

    points = (
        ()
        if route.status is RegimeRouteStatus.INVALID_POINT_IN_TIME
        else _directional_points(request)
    )
    score = _weighted_mean(points)
    source_disagreement = _weighted_std(points, score)
    council_disagreement = (
        float(request.research_council_snapshot.disagreement_score)
        if request.research_council_snapshot is not None
        else 0.0
    )
    disagreement = max(
        source_disagreement,
        council_disagreement,
        float(route.disagreement_score),
    )
    uncertainty = _aggregate_uncertainty(points, request)
    coverage = min(1.0, len(points) / _EXPECTED_DIRECTIONAL_SOURCES)

    reasons: list[str] = []
    blocking = False
    if pit_reasons:
        reasons.extend(
            f"invalid_point_in_time:{reason}" for reason in sorted(pit_reasons)
        )
        blocking = True

    if proposed_side == "no_trade":
        reasons.append("qlib_no_trade_or_missing_direction")

    ai_shadow = request.ai_shadow
    if (
        ai_shadow is not None
        and ai_shadow.decision is AIShadowDecision.VETO
        and float(ai_shadow.veto_score) >= float(config.ai_shadow_veto_score_threshold)
        and float(ai_shadow.confidence)
        >= float(config.ai_shadow_veto_confidence_threshold)
    ):
        reasons.append("ai_shadow_veto_high_confidence")

    council = request.research_council_snapshot
    if council is not None:
        if float(council.disagreement_score) >= float(
            config.high_disagreement_threshold
        ):
            reasons.append("research_council_high_disagreement")
        if float(council.uncertainty_score) >= float(config.high_uncertainty_threshold):
            reasons.append("research_council_high_uncertainty")
        if float(council.context_quality) < float(config.min_context_quality):
            reasons.append("research_council_low_context_quality")

    if disagreement >= float(config.high_disagreement_threshold):
        reasons.append("ensemble_high_disagreement")
    if uncertainty >= float(config.high_uncertainty_threshold):
        reasons.append("ensemble_high_uncertainty")

    if (
        alignment is RegimeAlignment.COUNTER_TREND
        and float(route.regime_confidence)
        >= float(config.counter_trend_abstain_confidence)
    ):
        reasons.append("counter_trend_high_confidence_regime")
    elif (
        alignment is RegimeAlignment.RANGE
        and float(route.regime_confidence) >= float(config.range_abstain_confidence)
    ):
        reasons.append("range_high_confidence_regime")

    if len(points) < int(config.min_directional_evidence_count):
        reasons.append("insufficient_directional_evidence")

    abstain_reasons = {
        "qlib_no_trade_or_missing_direction",
        "ai_shadow_veto_high_confidence",
        "research_council_high_disagreement",
        "research_council_high_uncertainty",
        "research_council_low_context_quality",
        "ensemble_high_disagreement",
        "ensemble_high_uncertainty",
        "counter_trend_high_confidence_regime",
        "range_high_confidence_regime",
        "insufficient_directional_evidence",
    }
    if blocking or any(reason in abstain_reasons for reason in reasons):
        action = ResearchAction.ABSTAIN
    elif (
        disagreement >= float(config.deprioritize_disagreement_threshold)
        or alignment
        in {
            RegimeAlignment.UNKNOWN,
            RegimeAlignment.RANGE,
            RegimeAlignment.COUNTER_TREND,
        }
        or route.status in {RegimeRouteStatus.PARTIAL, RegimeRouteStatus.UNKNOWN}
    ):
        action = ResearchAction.DEPRIORITIZE_RESEARCH
        if disagreement >= float(config.deprioritize_disagreement_threshold):
            reasons.append("moderate_disagreement_deprioritized")
        if alignment is RegimeAlignment.UNKNOWN:
            reasons.append("unknown_regime_deprioritized")
        if route.status is RegimeRouteStatus.PARTIAL:
            reasons.append("partial_regime_evidence_deprioritized")
    else:
        action = ResearchAction.PROCEED_RESEARCH
        reasons.append("research_evidence_consistent")

    if blocking:
        status = EnsembleStatus.BLOCKED
    elif action is ResearchAction.PROCEED_RESEARCH:
        status = EnsembleStatus.SUCCESS
    else:
        status = EnsembleStatus.PARTIAL

    valid_until = request.decision_time_utc + timedelta(
        seconds=config.default_ttl_seconds
    )
    if request.qlib is not None and request.qlib.valid_until_utc is not None:
        valid_until = min(valid_until, request.qlib.valid_until_utc)
    if request.ai_shadow is not None and request.ai_shadow.valid_until_utc is not None:
        valid_until = min(valid_until, request.ai_shadow.valid_until_utc)
    if request.research_council_snapshot is not None:
        valid_until = min(
            valid_until,
            request.research_council_snapshot.valid_until_utc,
        )

    source_hashes = _source_hashes(request)
    unique_reasons = tuple(sorted(set(reasons)))
    semantic = {
        "request_id": request.request_id,
        "symbol": request.symbol,
        "decision_time_utc": request.decision_time_utc,
        "proposed_side": proposed_side,
        "regime_route": route,
        "alignment": alignment,
        "ensemble_score": score,
        "disagreement": disagreement,
        "uncertainty": uncertainty,
        "coverage": coverage,
        "directional_evidence": points,
        "ai_shadow_decision": request.ai_shadow.decision if request.ai_shadow else None,
        "ai_shadow_veto_score": (
            request.ai_shadow.veto_score if request.ai_shadow else None
        ),
        "ai_shadow_confidence": (
            request.ai_shadow.confidence if request.ai_shadow else None
        ),
        "research_action": action,
        "reasons": unique_reasons,
        "source_hashes": source_hashes,
        "config": config,
    }
    decision_id = f"ensemble-abstention-{canonical_sha256(semantic)}"

    return EnsembleAbstentionDecision(
        decision_id=decision_id,
        request_id=request.request_id,
        symbol=request.symbol,
        decision_time_utc=request.decision_time_utc,
        created_at_utc=request.decision_time_utc,
        valid_until_utc=valid_until,
        status=status,
        research_action=action,
        reasons=unique_reasons,
        proposed_side=proposed_side,
        regime_route=route,
        regime_alignment=alignment,
        ensemble_score=score,
        disagreement_score=max(0.0, min(1.0, disagreement)),
        uncertainty_score=max(0.0, min(1.0, uncertainty)),
        evidence_coverage=max(0.0, min(1.0, coverage)),
        directional_evidence_count=len(points),
        directional_evidence=points,
        ai_shadow_decision=request.ai_shadow.decision if request.ai_shadow else None,
        ai_shadow_veto_score=(
            request.ai_shadow.veto_score if request.ai_shadow else None
        ),
        ai_shadow_confidence=(
            request.ai_shadow.confidence if request.ai_shadow else None
        ),
        source_hashes=source_hashes,
    )


def _directional_points(
    request: EnsembleAbstentionRequest,
) -> tuple[DirectionalEvidencePoint, ...]:
    points: list[DirectionalEvidencePoint] = []
    if request.qlib is not None:
        points.append(
            DirectionalEvidencePoint(
                source="qlib",
                score=float(request.qlib.score),
                confidence=float(request.qlib.confidence),
                uncertainty=1.0 - float(request.qlib.confidence),
                evidence_id=request.qlib.evidence_id,
            )
        )

    council = request.research_council_snapshot
    if council is not None and council.status != "BLOCKED_NO_VALID_CONTEXT":
        council_confidence = max(
            0.0,
            min(
                1.0,
                float(council.context_quality)
                * (1.0 - float(council.uncertainty_score)),
            ),
        )
        points.append(
            DirectionalEvidencePoint(
                source="research_council",
                score=float(council.consensus_score),
                confidence=council_confidence,
                uncertainty=float(council.uncertainty_score),
                evidence_id=council.snapshot_id,
            )
        )

    market = request.market_intelligence_snapshot
    if market is not None:
        flow_status = market.feature_family_statuses.get("flow")
        flow_value = market.flow_features.get("flow_imbalance_15s")
        if (
            flow_status is not None
            and flow_status.status.value == "FRESH"
            and isinstance(flow_value, (int, float))
        ):
            market_confidence = max(0.0, min(1.0, float(market.coverage)))
            points.append(
                DirectionalEvidencePoint(
                    source="market_intelligence",
                    score=max(-1.0, min(1.0, float(flow_value))),
                    confidence=market_confidence,
                    uncertainty=1.0 - market_confidence,
                    evidence_id=market.snapshot_id,
                )
            )

    return tuple(points)


def _weighted_mean(points: tuple[DirectionalEvidencePoint, ...]) -> float:
    if not points:
        return 0.0
    weights = [max(1e-9, float(point.confidence)) for point in points]
    total = sum(weights)
    weighted_sum = sum(
        float(point.score) * weight for point, weight in zip(points, weights)
    )
    return max(-1.0, min(1.0, weighted_sum / total))


def _weighted_std(
    points: tuple[DirectionalEvidencePoint, ...],
    mean: float,
) -> float:
    if len(points) <= 1:
        return 0.0
    weights = [max(1e-9, float(point.confidence)) for point in points]
    total = sum(weights)
    variance = sum(
        weight * (float(point.score) - mean) ** 2
        for point, weight in zip(points, weights)
    ) / total
    return max(0.0, min(1.0, math.sqrt(max(0.0, variance))))


def _aggregate_uncertainty(
    points: tuple[DirectionalEvidencePoint, ...],
    request: EnsembleAbstentionRequest,
) -> float:
    values = [float(point.uncertainty) for point in points]
    if request.research_council_snapshot is not None:
        values.append(float(request.research_council_snapshot.uncertainty_score))
    if not values:
        return 1.0
    return max(0.0, min(1.0, fmean(values)))


def _alignment(side: str, regime: RegimeLabel) -> RegimeAlignment:
    if side not in {"long", "short"}:
        return RegimeAlignment.NOT_APPLICABLE
    if regime is RegimeLabel.RANGE:
        return RegimeAlignment.RANGE
    if regime is RegimeLabel.UNKNOWN:
        return RegimeAlignment.UNKNOWN
    if (side == "long" and regime is RegimeLabel.TREND_UP) or (
        side == "short" and regime is RegimeLabel.TREND_DOWN
    ):
        return RegimeAlignment.ALIGNED
    return RegimeAlignment.COUNTER_TREND


def _source_hashes(request: EnsembleAbstentionRequest) -> tuple[str, ...]:
    hashes: set[str] = set()
    if request.qlib is not None:
        hashes.add(request.qlib.source_hash)
    if request.ai_shadow is not None:
        hashes.add(request.ai_shadow.source_hash)
    if request.research_council_snapshot is not None:
        hashes.add(canonical_sha256(request.research_council_snapshot))
    if request.market_intelligence_snapshot is not None:
        hashes.add(canonical_sha256(request.market_intelligence_snapshot))
    return tuple(sorted(hashes))
