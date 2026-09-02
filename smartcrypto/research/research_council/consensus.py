"""Deterministic structured consensus without autonomous model authority."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from statistics import fmean

from .contracts import (
    AgentResult,
    AgentStatus,
    ConsensusResult,
    NewsAnalysis,
    require_utc,
)
from .debate import structured_agent_signals


def build_consensus(
    results: tuple[AgentResult, ...],
    *,
    decision_time_utc: datetime,
    default_ttl_seconds: int,
    min_valid_agents: int,
) -> ConsensusResult:
    signals = structured_agent_signals(results)
    valid_count = len(signals)
    if valid_count:
        consensus = fmean(item[1] for item in signals)
        uncertainty = fmean(item[2] for item in signals)
        variance = fmean((item[1] - consensus) ** 2 for item in signals)
        disagreement = min(1.0, math.sqrt(variance))
    else:
        consensus = 0.0
        uncertainty = 1.0
        disagreement = 1.0
    coverage = valid_count / 5.0
    quality = max(0.0, min(1.0, coverage * (1.0 - 0.5 * uncertainty - 0.5 * disagreement)))
    if valid_count == 0:
        status = "BLOCKED_NO_VALID_CONTEXT"
    elif valid_count < min_valid_agents:
        status = "PARTIAL"
    else:
        status = "SUCCESS"

    ttl_candidates = [default_ttl_seconds]
    for result in results:
        if result.context_type != "news" or result.status is not AgentStatus.SUCCESS:
            continue
        if result.context_payload is not None:
            ttl_candidates.append(NewsAnalysis.model_validate(result.context_payload).ttl_seconds)
    ttl_seconds = min(ttl_candidates)
    decision = require_utc(decision_time_utc)
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
    return ConsensusResult(
        status=status,
        consensus_score=consensus,
        disagreement_score=disagreement,
        uncertainty_score=uncertainty,
        context_quality=quality,
        ttl_seconds=ttl_seconds,
        valid_until_utc=decision + timedelta(seconds=ttl_seconds),
        evidence_ids=evidence_ids,
        agent_statuses={result.agent_id: result.status for result in results},
    )

