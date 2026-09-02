"""Deterministic point-in-time regime router for W4 research."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from smartcrypto.research.research_council.contracts import RegimeAnalysis

from .contracts import (
    EnsembleAbstentionRequest,
    RegimeEvidencePoint,
    RegimeLabel,
    RegimeRoute,
    RegimeRouteStatus,
    require_utc,
)


def normalize_regime_label(value: object) -> tuple[RegimeLabel, bool]:
    text = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    high_vol = any(marker in text for marker in ("high_vol", "highvol", "high_volatility"))
    if any(marker in text for marker in ("trend_up", "uptrend", "bull", "bullish")):
        return RegimeLabel.TREND_UP, high_vol
    if any(marker in text for marker in ("trend_down", "downtrend", "bear", "bearish", "risk_off")):
        return RegimeLabel.TREND_DOWN, high_vol
    if any(marker in text for marker in ("range", "sideways", "neutral", "flat")):
        return RegimeLabel.RANGE, high_vol
    return RegimeLabel.UNKNOWN, high_vol


def build_regime_route(
    request: EnsembleAbstentionRequest,
    *,
    default_regime_confidence_when_missing: float,
) -> RegimeRoute:
    decision_time = require_utc(request.decision_time_utc)
    pit_errors = _point_in_time_errors(request, decision_time)
    if pit_errors:
        return RegimeRoute(
            status=RegimeRouteStatus.INVALID_POINT_IN_TIME,
            regime_label=RegimeLabel.UNKNOWN,
            regime_confidence=0.0,
            trend_score=0.0,
            range_score=0.0,
            volatility_score=0.0,
            high_volatility=False,
            disagreement_score=1.0,
            evidence_points=(),
            evidence_ids=(),
            point_in_time_valid=False,
            reason=";".join(sorted(pit_errors)),
        )

    points: list[RegimeEvidencePoint] = []

    if request.qlib is not None:
        qlib = request.qlib
        label, high_vol = normalize_regime_label(qlib.market_regime)
        if label is not RegimeLabel.UNKNOWN and qlib.market_regime_status in {
            "fresh",
            "point_in_time",
        }:
            confidence = (
                float(qlib.market_regime_confidence)
                if qlib.market_regime_confidence is not None
                else float(default_regime_confidence_when_missing)
            )
            points.append(
                RegimeEvidencePoint(
                    source="qlib_market_regime",
                    regime_label=label,
                    confidence=confidence,
                    volatility_score=1.0 if high_vol else 0.0,
                    high_volatility=high_vol,
                    evidence_id=qlib.evidence_id,
                )
            )

    council = request.research_council_snapshot
    if council is not None and council.regime_context is not None:
        context = RegimeAnalysis.model_validate(council.regime_context)
        label, label_high_vol = normalize_regime_label(context.regime_label)
        if label is not RegimeLabel.UNKNOWN:
            confidence = max(
                0.0,
                min(1.0, float(context.regime_confidence) * (1.0 - float(context.uncertainty))),
            )
            points.append(
                RegimeEvidencePoint(
                    source="research_council_regime",
                    regime_label=label,
                    confidence=confidence,
                    volatility_score=float(context.volatility_score),
                    high_volatility=label_high_vol or float(context.volatility_score) >= 0.75,
                    evidence_id=council.snapshot_id,
                )
            )

    if not points:
        return RegimeRoute(
            status=RegimeRouteStatus.UNKNOWN,
            regime_label=RegimeLabel.UNKNOWN,
            regime_confidence=0.0,
            trend_score=0.0,
            range_score=0.0,
            volatility_score=0.0,
            high_volatility=False,
            disagreement_score=1.0,
            evidence_points=(),
            evidence_ids=(),
            point_in_time_valid=True,
            reason="no_valid_regime_evidence",
        )

    weights: dict[RegimeLabel, float] = defaultdict(float)
    total_weight = 0.0
    for point in points:
        weight = max(1e-9, float(point.confidence))
        weights[point.regime_label] += weight
        total_weight += weight

    winner = sorted(
        weights.items(),
        key=lambda item: (-item[1], item[0].value),
    )[0][0]
    winner_weight = weights[winner]
    agreement = winner_weight / total_weight if total_weight > 0.0 else 0.0
    average_confidence = total_weight / len(points)
    route_confidence = max(0.0, min(1.0, agreement * average_confidence))
    disagreement = max(0.0, min(1.0, 1.0 - agreement))

    signed = {
        RegimeLabel.TREND_UP: 1.0,
        RegimeLabel.TREND_DOWN: -1.0,
        RegimeLabel.RANGE: 0.0,
        RegimeLabel.UNKNOWN: 0.0,
    }
    trend_score = sum(signed[point.regime_label] * float(point.confidence) for point in points)
    trend_score = trend_score / total_weight if total_weight > 0.0 else 0.0
    range_score = sum(
        (1.0 if point.regime_label is RegimeLabel.RANGE else 0.0) * float(point.confidence)
        for point in points
    )
    range_score = range_score / total_weight if total_weight > 0.0 else 0.0
    volatility_score = sum(
        float(point.volatility_score) * float(point.confidence) for point in points
    )
    volatility_score = volatility_score / total_weight if total_weight > 0.0 else 0.0
    high_volatility = any(point.high_volatility for point in points)

    return RegimeRoute(
        status=(RegimeRouteStatus.SUCCESS if len(points) >= 2 else RegimeRouteStatus.PARTIAL),
        regime_label=winner,
        regime_confidence=route_confidence,
        trend_score=max(-1.0, min(1.0, trend_score)),
        range_score=max(0.0, min(1.0, range_score)),
        volatility_score=max(0.0, min(1.0, volatility_score)),
        high_volatility=high_volatility,
        disagreement_score=disagreement,
        evidence_points=tuple(points),
        evidence_ids=tuple(sorted({point.evidence_id for point in points})),
        point_in_time_valid=True,
        reason=("regime_routed" if len(points) >= 2 else "regime_routed_from_single_source"),
    )


def _point_in_time_errors(
    request: EnsembleAbstentionRequest,
    decision_time_utc: datetime,
) -> tuple[str, ...]:
    errors: list[str] = []
    if request.qlib is not None:
        errors.extend(request.qlib.point_in_time_errors(decision_time_utc))
    council = request.research_council_snapshot
    if council is not None:
        if council.decision_time_utc > decision_time_utc:
            errors.append("council_decision_time_after_ensemble_decision")
        if council.available_at_utc > decision_time_utc:
            errors.append("council_available_at_after_ensemble_decision")
        if council.valid_until_utc < decision_time_utc:
            errors.append("council_snapshot_expired")
    market = request.market_intelligence_snapshot
    if market is not None:
        if market.decision_time_utc > decision_time_utc:
            errors.append("market_intelligence_decision_time_after_ensemble_decision")
        if not market.point_in_time_valid:
            errors.append("market_intelligence_not_point_in_time_valid")
        for watermark in market.source_watermarks:
            if watermark.max_available_at_utc > decision_time_utc:
                errors.append(
                    f"market_intelligence_source_after_ensemble_decision:{watermark.event_type}"
                )
    if request.ai_shadow is not None:
        errors.extend(request.ai_shadow.point_in_time_errors(decision_time_utc))
    return tuple(sorted(set(errors)))
