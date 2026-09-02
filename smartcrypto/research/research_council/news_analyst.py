"""Deterministic supplied-news analyst; it never fetches external news."""

from __future__ import annotations

from datetime import datetime

from .contracts import AgentResult, AgentStatus, NewsAnalysis, StructuredEvidenceInput
from .provider_adapter import ProviderAdapter, execute_structured_agent

AGENT_ID = "news_analyst_v1"


def analyze_news(
    evidence: tuple[StructuredEvidenceInput, ...],
    adapter: ProviderAdapter,
    decision_time_utc: datetime,
) -> AgentResult:
    result, evidence_ids = execute_structured_agent(
        agent_id=AGENT_ID,
        context_type="news",
        evidence=evidence,
        response_model=NewsAnalysis,
        adapter=adapter,
        decision_time_utc=decision_time_utc,
    )
    if result is None:
        return AgentResult(
            agent_id=AGENT_ID, context_type="news", status=AgentStatus.MISSING_CONTEXT
        )
    status = (
        AgentStatus.SUCCESS
        if isinstance(result.response, NewsAnalysis)
        else AgentStatus.INVALID_RESPONSE
    )
    return AgentResult(
        agent_id=AGENT_ID,
        context_type="news",
        status=status,
        evidence_ids=evidence_ids,
        errors=() if status is AgentStatus.SUCCESS else (result.audit.error_reason or "error",),
        context_payload=(
            result.response.model_dump(mode="json") if result.response is not None else None
        ),
        provider_audit=result.audit,
    )
