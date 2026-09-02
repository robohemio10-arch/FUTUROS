"""Deterministic bull, bear, and neutral debate projection."""

from __future__ import annotations

from statistics import fmean

from .contracts import (
    AgentResult,
    AgentStatus,
    DebateCase,
    MacroAnalysis,
    MarketAnalysis,
    MicrostructureAnalysis,
    NewsAnalysis,
    RegimeAnalysis,
)


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return min(high, max(low, float(value)))


def structured_agent_signals(
    results: tuple[AgentResult, ...],
) -> tuple[tuple[str, float, float], ...]:
    signals: list[tuple[str, float, float]] = []
    for result in results:
        if result.status is not AgentStatus.SUCCESS or result.context_payload is None:
            continue
        payload = result.context_payload
        if result.context_type == "market":
            context = MarketAnalysis.model_validate(payload)
            direction = fmean(
                (
                    context.trend_strength,
                    context.momentum_score,
                    context.support_pressure - context.resistance_pressure,
                )
            )
            uncertainty = context.uncertainty
        elif result.context_type == "microstructure":
            context = MicrostructureAnalysis.model_validate(payload)
            direction = context.flow_pressure * (1.0 - context.spread_stress)
            uncertainty = context.microstructure_uncertainty
        elif result.context_type == "news":
            context = NewsAnalysis.model_validate(payload)
            direction = context.sentiment_score * context.severity
            uncertainty = context.uncertainty
        elif result.context_type == "macro":
            context = MacroAnalysis.model_validate(payload)
            direction = context.risk_on_off_score * (1.0 - context.event_shock_score)
            uncertainty = context.uncertainty
        else:
            context = RegimeAnalysis.model_validate(payload)
            label = context.regime_label.casefold()
            sign = -1.0 if any(marker in label for marker in ("down", "bear", "risk_off")) else 1.0
            if any(marker in label for marker in ("range", "neutral", "sideways")):
                sign = 0.0
            direction = sign * context.trend_score * context.regime_confidence
            uncertainty = context.uncertainty
        signals.append((result.agent_id, _clip(direction, -1.0, 1.0), uncertainty))
    return tuple(signals)


def build_debate(
    results: tuple[AgentResult, ...],
) -> tuple[DebateCase, DebateCase, DebateCase]:
    signals = structured_agent_signals(results)
    evidence_ids = tuple(
        sorted(
            {
                evidence_id
                for result in results
                if result.status is AgentStatus.SUCCESS
                for evidence_id in result.evidence_ids
            }
        )
    )
    if signals:
        directional = fmean(item[1] for item in signals)
        uncertainty = fmean(item[2] for item in signals)
    else:
        directional = 0.0
        uncertainty = 1.0
    bull_score = _clip(((directional + 1.0) / 2.0) * (1.0 - 0.25 * uncertainty))
    bear_score = _clip(((-directional + 1.0) / 2.0) * (1.0 - 0.25 * uncertainty))
    neutral_score = _clip(max(uncertainty, 1.0 - abs(directional)))
    summary = (
        f"valid_agents={len(signals)};directional_mean={directional:.6f};"
        f"uncertainty_mean={uncertainty:.6f}"
    )
    return (
        DebateCase(
            stance="BULL",
            score=bull_score,
            evidence_ids=evidence_ids,
            reasoning_summary=summary,
        ),
        DebateCase(
            stance="BEAR",
            score=bear_score,
            evidence_ids=evidence_ids,
            reasoning_summary=summary,
        ),
        DebateCase(
            stance="NEUTRAL",
            score=neutral_score,
            evidence_ids=evidence_ids,
            reasoning_summary=summary,
        ),
    )

