"""Deterministic supplied-macro-context analyst."""

from __future__ import annotations

from datetime import datetime

from .contracts import AgentResult, AgentStatus, MacroAnalysis, StructuredEvidenceInput
from .provider_adapter import ProviderAdapter, execute_structured_agent

AGENT_ID = "macro_analyst_v1"


def analyze_macro(
    evidence: tuple[StructuredEvidenceInput, ...],
    adapter: ProviderAdapter,
    decision_time_utc: datetime,
) -> AgentResult:
    result, evidence_ids = execute_structured_agent(
        agent_id=AGENT_ID,
        context_type="macro",
        evidence=evidence,
        response_model=MacroAnalysis,
        adapter=adapter,
        decision_time_utc=decision_time_utc,
    )
    if result is None:
        return AgentResult(
            agent_id=AGENT_ID, context_type="macro", status=AgentStatus.MISSING_CONTEXT
        )
    status = (
        AgentStatus.SUCCESS
        if isinstance(result.response, MacroAnalysis)
        else AgentStatus.INVALID_RESPONSE
    )
    return AgentResult(
        agent_id=AGENT_ID,
        context_type="macro",
        status=status,
        evidence_ids=evidence_ids,
        errors=() if status is AgentStatus.SUCCESS else (result.audit.error_reason or "error",),
        context_payload=(
            result.response.model_dump(mode="json") if result.response is not None else None
        ),
        provider_audit=result.audit,
    )
