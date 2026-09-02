"""Research-only orchestration for structured context intelligence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .consensus import build_consensus
from .contracts import (
    AgentResult,
    AgentStatus,
    ContextIntelligenceSnapshot,
    CouncilRequest,
    CouncilRunReport,
    MacroAnalysis,
    MarketAnalysis,
    MicrostructureAnalysis,
    NewsAnalysis,
    RegimeAnalysis,
    ResearchCouncilConfig,
    SourceProvenance,
    StructuredEvidenceInput,
    canonical_sha256,
)
from .debate import build_debate
from .macro_analyst import analyze_macro
from .market_analyst import analyze_market
from .microstructure_analyst import analyze_microstructure
from .news_analyst import analyze_news
from .persistence import ResearchCouncilPersistenceError, persist_snapshot
from .provider_adapter import (
    DeterministicOfflineProvider,
    ProviderAdapter,
    ProviderExecutionPolicy,
)
from .provider_registry import ProviderRegistry
from .regime_analyst import analyze_regime

MAX_CONFIG_BYTES = 256 * 1024


def load_research_council_config(
    project_root: str | Path,
    source: str | Path | dict[str, Any] | ResearchCouncilConfig,
) -> ResearchCouncilConfig:
    if isinstance(source, ResearchCouncilConfig):
        return source
    if isinstance(source, dict):
        return ResearchCouncilConfig.model_validate(source)
    root = Path(project_root).resolve()
    path = Path(source)
    path = path if path.is_absolute() else root / path
    path = path.resolve(strict=False)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("research_council_config_outside_project") from exc
    if path.is_symlink():
        raise ValueError("research_council_config_symlink_forbidden")
    if not path.is_file():
        raise ValueError("research_council_config_missing")
    if path.suffix.casefold() not in {".yml", ".yaml"}:
        raise ValueError("research_council_config_extension_invalid")
    if path.stat().st_size > MAX_CONFIG_BYTES:
        raise ValueError("research_council_config_too_large")
    payload = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    if not isinstance(payload, dict):
        raise ValueError("research_council_config_root_must_be_mapping")
    return ResearchCouncilConfig.model_validate(payload)


def build_default_adapter(config: ResearchCouncilConfig) -> ProviderAdapter:
    registry = ProviderRegistry()
    registry.register(DeterministicOfflineProvider(enabled=config.provider_enabled))
    backend = registry.get(config.provider_id)
    return ProviderAdapter(
        backend,
        ProviderExecutionPolicy(
            timeout_seconds=config.provider_timeout_seconds,
            max_retry_attempts=config.max_retry_attempts,
            retry_backoff_seconds=config.retry_backoff_seconds,
            circuit_breaker_failure_threshold=config.circuit_breaker_failure_threshold,
            circuit_breaker_cooldown_seconds=config.circuit_breaker_cooldown_seconds,
            cache_ttl_seconds=config.cache_ttl_seconds,
        ),
    )


class ResearchCouncilService:
    def __init__(
        self,
        config: ResearchCouncilConfig,
        *,
        adapter: ProviderAdapter | None = None,
    ) -> None:
        self.config = config
        self.adapter = adapter or build_default_adapter(config)

    def evaluate(
        self,
        request: CouncilRequest,
        *,
        project_root: str | Path,
        write_report: bool = False,
        output_json: str | Path | None = None,
    ) -> CouncilRunReport:
        valid_evidence, provenance = _validate_point_in_time(request)
        results: tuple[AgentResult, ...] = (
            analyze_market(valid_evidence, self.adapter, request.decision_time_utc),
            analyze_microstructure(valid_evidence, self.adapter, request.decision_time_utc),
            analyze_news(valid_evidence, self.adapter, request.decision_time_utc),
            analyze_macro(valid_evidence, self.adapter, request.decision_time_utc),
            analyze_regime(valid_evidence, self.adapter, request.decision_time_utc),
        )
        results = _mark_invalid_point_in_time_contexts(results, request.evidence, provenance)
        bull_case, bear_case, neutral_case = build_debate(results)
        consensus = build_consensus(
            results,
            decision_time_utc=request.decision_time_utc,
            default_ttl_seconds=self.config.default_ttl_seconds,
            min_valid_agents=self.config.min_valid_agents,
        )
        available_at = max(
            (item.available_at_utc for item in valid_evidence),
            default=request.decision_time_utc,
        )
        contexts = _typed_contexts(results)
        provider_provenance = tuple(
            result.provider_audit
            for result in results
            if result.provider_audit is not None
        )
        semantic_payload = {
            "schema_version": "context_intelligence_snapshot_v1",
            "symbol": request.symbol,
            "decision_time_utc": request.decision_time_utc,
            "available_at_utc": available_at,
            "valid_until_utc": consensus.valid_until_utc,
            "ttl_seconds": consensus.ttl_seconds,
            "contexts": {
                key: value.model_dump(mode="json") if value is not None else None
                for key, value in contexts.items()
            },
            "debate": [
                bull_case.model_dump(mode="json"),
                bear_case.model_dump(mode="json"),
                neutral_case.model_dump(mode="json"),
            ],
            "consensus": consensus.model_dump(mode="json"),
            "source_provenance": [item.model_dump(mode="json") for item in provenance],
        }
        snapshot_id = f"research-context-{canonical_sha256(semantic_payload)}"
        snapshot = ContextIntelligenceSnapshot(
            snapshot_id=snapshot_id,
            status=consensus.status,
            reason=(
                "no_valid_point_in_time_context"
                if consensus.status == "BLOCKED_NO_VALID_CONTEXT"
                else ("insufficient_valid_agents" if consensus.status == "PARTIAL" else None)
            ),
            symbol=request.symbol,
            decision_time_utc=request.decision_time_utc,
            created_at_utc=request.decision_time_utc,
            available_at_utc=available_at,
            valid_until_utc=consensus.valid_until_utc,
            ttl_seconds=consensus.ttl_seconds,
            market_context=contexts["market_context"],
            microstructure_context=contexts["microstructure_context"],
            news_context=contexts["news_context"],
            macro_context=contexts["macro_context"],
            regime_context=contexts["regime_context"],
            bull_case=bull_case,
            bear_case=bear_case,
            neutral_case=neutral_case,
            consensus_score=consensus.consensus_score,
            disagreement_score=consensus.disagreement_score,
            uncertainty_score=consensus.uncertainty_score,
            context_quality=consensus.context_quality,
            provider_provenance=provider_provenance,
            source_provenance=provenance,
            evidence_ids=consensus.evidence_ids,
            agent_statuses=consensus.agent_statuses,
        )
        write_performed = False
        output_paths: dict[str, str] = {}
        if write_report:
            try:
                persisted = persist_snapshot(
                    project_root=project_root,
                    snapshot=snapshot,
                    output_json=output_json,
                )
            except ResearchCouncilPersistenceError as exc:
                return CouncilRunReport(
                    status="BLOCKED",
                    reason=exc.reason,
                    request_id=request.request_id,
                    input_evidence_count=len(request.evidence),
                    valid_point_in_time_evidence_count=len(valid_evidence),
                    invalid_point_in_time_evidence_count=len(request.evidence) - len(valid_evidence),
                    snapshot=snapshot,
                    write_requested=True,
                    write_performed=exc.write_performed,
                )
            write_performed = bool(persisted["write_performed"])
            output_paths = dict(persisted["output_paths"])
        return CouncilRunReport(
            status=consensus.status,
            reason=snapshot.reason,
            request_id=request.request_id,
            input_evidence_count=len(request.evidence),
            valid_point_in_time_evidence_count=len(valid_evidence),
            invalid_point_in_time_evidence_count=len(request.evidence) - len(valid_evidence),
            snapshot=snapshot,
            write_requested=write_report,
            write_performed=write_performed,
            output_paths=output_paths,
        )


def blocked_report(reason: str, *, write_requested: bool = False) -> CouncilRunReport:
    return CouncilRunReport(
        status="BLOCKED",
        reason=reason,
        request_id=None,
        input_evidence_count=0,
        valid_point_in_time_evidence_count=0,
        invalid_point_in_time_evidence_count=0,
        snapshot=None,
        write_requested=write_requested,
        write_performed=False,
    )


def _validate_point_in_time(
    request: CouncilRequest,
) -> tuple[tuple[StructuredEvidenceInput, ...], tuple[SourceProvenance, ...]]:
    valid: list[StructuredEvidenceInput] = []
    provenance: list[SourceProvenance] = []
    for item in request.evidence:
        errors = item.point_in_time_errors(request.decision_time_utc)
        if not errors:
            valid.append(item)
        provenance.append(
            SourceProvenance(
                event_id=item.event_id,
                context_type=item.context_type,
                source_id=item.source_id,
                source_hash=item.source_hash,
                available_at_utc=item.available_at_utc,
                point_in_time_valid=not errors,
                validation_errors=errors,
            )
        )
    return tuple(valid), tuple(provenance)


def _mark_invalid_point_in_time_contexts(
    results: tuple[AgentResult, ...],
    all_evidence: tuple[StructuredEvidenceInput, ...],
    provenance: tuple[SourceProvenance, ...],
) -> tuple[AgentResult, ...]:
    invalid_by_context: dict[str, list[SourceProvenance]] = {}
    valid_contexts = {item.context_type for item in provenance if item.point_in_time_valid}
    for item in provenance:
        if not item.point_in_time_valid:
            invalid_by_context.setdefault(item.context_type, []).append(item)
    present_contexts = {item.context_type for item in all_evidence}
    adjusted: list[AgentResult] = []
    for result in results:
        if (
            result.status is AgentStatus.MISSING_CONTEXT
            and result.context_type in present_contexts
            and result.context_type not in valid_contexts
        ):
            invalid = invalid_by_context.get(result.context_type, [])
            adjusted.append(
                AgentResult(
                    agent_id=result.agent_id,
                    context_type=result.context_type,
                    status=AgentStatus.INVALID_POINT_IN_TIME,
                    evidence_ids=tuple(item.event_id for item in invalid),
                    errors=tuple(
                        error for item in invalid for error in item.validation_errors
                    ),
                )
            )
        else:
            adjusted.append(result)
    return tuple(adjusted)


def _typed_contexts(results: tuple[AgentResult, ...]) -> dict[str, Any]:
    models = {
        "market": MarketAnalysis,
        "microstructure": MicrostructureAnalysis,
        "news": NewsAnalysis,
        "macro": MacroAnalysis,
        "regime": RegimeAnalysis,
    }
    contexts: dict[str, Any] = {
        "market_context": None,
        "microstructure_context": None,
        "news_context": None,
        "macro_context": None,
        "regime_context": None,
    }
    for result in results:
        if result.status is AgentStatus.SUCCESS and result.context_payload is not None:
            try:
                contexts[f"{result.context_type}_context"] = models[
                    result.context_type
                ].model_validate(result.context_payload)
            except ValidationError:
                contexts[f"{result.context_type}_context"] = None
    return contexts
