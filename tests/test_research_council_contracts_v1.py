from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from smartcrypto.research.research_council import (
    ContextIntelligenceSnapshot,
    CouncilRequest,
    DeterministicOfflineProvider,
    MarketAnalysis,
    ProviderAdapter,
    ProviderExecutionPolicy,
    ProviderRateLimitError,
    ProviderStatus,
    ProviderTimeoutError,
    ResearchCouncilConfig,
    ResearchCouncilService,
    StructuredEvidenceInput,
)
UTC = timezone.utc
DECISION_TIME = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
SOURCE_HASH = "a" * 64


def _payload(context_type: str) -> dict[str, Any]:
    payloads = {
        "market": {
            "trend_strength": 0.6,
            "momentum_score": 0.4,
            "volatility_state": "normal",
            "support_pressure": 0.7,
            "resistance_pressure": 0.3,
            "uncertainty": 0.2,
        },
        "microstructure": {
            "flow_pressure": 0.5,
            "spread_stress": 0.1,
            "liquidity_state": "deep",
            "basis_state": "stable",
            "microstructure_uncertainty": 0.2,
        },
        "news": {
            "event_type": "scheduled_release",
            "sentiment_score": 0.2,
            "severity": 0.5,
            "affected_assets": ["BTCUSDT"],
            "unexpectedness": 0.1,
            "expected_duration_seconds": 600,
            "uncertainty": 0.3,
            "ttl_seconds": 600,
        },
        "macro": {
            "risk_on_off_score": 0.3,
            "event_shock_score": 0.1,
            "macro_regime": "risk_on",
            "horizon_seconds": 1800,
            "uncertainty": 0.25,
        },
        "regime": {
            "regime_label": "trend_up",
            "regime_confidence": 0.8,
            "trend_score": 0.7,
            "range_score": 0.2,
            "volatility_score": 0.4,
            "uncertainty": 0.15,
        },
    }
    return payloads[context_type]


def _evidence(
    context_type: str,
    *,
    suffix: str = "1",
    available_at: datetime | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    available = available_at or DECISION_TIME - timedelta(seconds=30)
    return {
        "schema_version": "research_council_evidence_v1",
        "event_id": f"{context_type}-event-{suffix}",
        "context_type": context_type,
        "symbol": "BTCUSDT",
        "event_time_utc": (DECISION_TIME - timedelta(minutes=2)).isoformat(),
        "published_at_utc": (DECISION_TIME - timedelta(minutes=1)).isoformat(),
        "ingested_at_utc": (DECISION_TIME - timedelta(seconds=45)).isoformat(),
        "available_at_utc": available.isoformat(),
        "source_id": f"fixture-{context_type}",
        "source_hash": SOURCE_HASH,
        "provenance": {"fixture": "true"},
        "payload": payload or _payload(context_type),
    }


def _request(*contexts: str) -> CouncilRequest:
    return CouncilRequest.model_validate(
        {
            "request_id": "fixture-request",
            "symbol": "BTCUSDT",
            "decision_time_utc": DECISION_TIME.isoformat(),
            "evidence": [_evidence(context) for context in contexts],
        }
    )


def _config(**changes: Any) -> ResearchCouncilConfig:
    return ResearchCouncilConfig.model_validate(changes)


def _policy(**changes: Any) -> ProviderExecutionPolicy:
    values = {
        "timeout_seconds": 1.0,
        "max_retry_attempts": 1,
        "retry_backoff_seconds": 0.0,
        "circuit_breaker_failure_threshold": 2,
        "circuit_breaker_cooldown_seconds": 30,
        "cache_ttl_seconds": 10,
    }
    values.update(changes)
    return ProviderExecutionPolicy(**values)


def test_valid_input_and_all_agent_output_schemas() -> None:
    report = ResearchCouncilService(_config()).evaluate(
        _request("market", "microstructure", "news", "macro", "regime"),
        project_root=".",
    )
    assert report.status == "SUCCESS"
    assert report.snapshot is not None
    assert report.snapshot.market_context is not None
    assert report.snapshot.microstructure_context is not None
    assert report.snapshot.news_context is not None
    assert report.snapshot.macro_context is not None
    assert report.snapshot.regime_context is not None
    assert report.snapshot.bull_case.stance == "BULL"
    assert report.snapshot.bear_case.stance == "BEAR"
    assert report.snapshot.neutral_case.stance == "NEUTRAL"


def test_invalid_schema_and_missing_critical_field_are_rejected() -> None:
    raw = _evidence("market")
    raw.pop("source_hash")
    with pytest.raises(ValidationError):
        StructuredEvidenceInput.model_validate(raw)
    with pytest.raises(ValidationError):
        CouncilRequest.model_validate(
            {"request_id": "x", "symbol": "BTCUSDT", "evidence": []}
        )


def test_future_outcome_and_sensitive_fields_are_rejected() -> None:
    for key in ("net_pnl", "api_token"):
        raw = _evidence("market", payload={**_payload("market"), key: "synthetic"})
        with pytest.raises(ValidationError):
            StructuredEvidenceInput.model_validate(raw)


def test_available_after_decision_blocks_point_in_time_context() -> None:
    request = CouncilRequest.model_validate(
        {
            "request_id": "future-request",
            "symbol": "BTCUSDT",
            "decision_time_utc": DECISION_TIME.isoformat(),
            "evidence": [
                _evidence(
                    "market",
                    available_at=DECISION_TIME + timedelta(seconds=1),
                )
            ],
        }
    )
    report = ResearchCouncilService(_config()).evaluate(request, project_root=".")
    assert report.status == "BLOCKED_NO_VALID_CONTEXT"
    assert report.invalid_point_in_time_evidence_count == 1
    assert report.snapshot is not None
    assert report.snapshot.agent_statuses["market_analyst_v1"].value == (
        "INVALID_POINT_IN_TIME"
    )


def test_deterministic_snapshot_id_and_semantic_payload() -> None:
    request = _request("market", "microstructure", "regime")
    first = ResearchCouncilService(_config()).evaluate(request, project_root=".")
    second = ResearchCouncilService(_config()).evaluate(request, project_root=".")
    assert first.snapshot is not None and second.snapshot is not None
    assert first.snapshot.snapshot_id == second.snapshot.snapshot_id
    assert first.snapshot.model_dump(mode="json") == second.snapshot.model_dump(mode="json")


def test_ttl_uses_shorter_news_ttl() -> None:
    report = ResearchCouncilService(_config(default_ttl_seconds=900)).evaluate(
        _request("market", "news", "regime"), project_root="."
    )
    assert report.snapshot is not None
    assert report.snapshot.ttl_seconds == 600
    assert report.snapshot.valid_until_utc == DECISION_TIME + timedelta(seconds=600)


def test_invalid_score_range_is_rejected() -> None:
    raw = _evidence("market")
    raw["payload"]["trend_strength"] = 1.1
    report = ResearchCouncilService(_config()).evaluate(
        CouncilRequest.model_validate(
            {
                "request_id": "invalid-score",
                "symbol": "BTCUSDT",
                "decision_time_utc": DECISION_TIME.isoformat(),
                "evidence": [raw],
            }
        ),
        project_root=".",
    )
    assert report.status == "BLOCKED_NO_VALID_CONTEXT"
    assert report.snapshot is not None
    assert report.snapshot.agent_statuses["market_analyst_v1"].value == "INVALID_RESPONSE"


def test_missing_optional_context_degrades_quality_without_invention() -> None:
    report = ResearchCouncilService(_config(min_valid_agents=3)).evaluate(
        _request("market"), project_root="."
    )
    assert report.status == "PARTIAL"
    assert report.snapshot is not None
    assert report.snapshot.news_context is None
    assert report.snapshot.context_quality < 0.2


class _ScriptedProvider:
    provider_id = "scripted"
    provider_type = "offline_test"
    model_id = "scripted"
    model_version = "1"
    enabled = True

    def __init__(self, actions: list[object]) -> None:
        self.actions = actions
        self.calls = 0

    def invoke(
        self,
        request: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        del request
        assert timeout_seconds > 0.0
        action = self.actions[min(self.calls, len(self.actions) - 1)]
        self.calls += 1
        if isinstance(action, Exception):
            raise action
        assert isinstance(action, dict)
        return action


class _MutableClock:
    def __init__(self) -> None:
        self.value = DECISION_TIME

    def __call__(self) -> datetime:
        return self.value


def _market_response() -> dict[str, Any]:
    return _payload("market")


def test_deterministic_provider_and_cache_hit() -> None:
    backend = DeterministicOfflineProvider()
    adapter = ProviderAdapter(backend, _policy())
    request = {"structured_response": _market_response()}
    first = adapter.execute(request, MarketAnalysis, request_time_utc=DECISION_TIME)
    second = adapter.execute(request, MarketAnalysis, request_time_utc=DECISION_TIME)
    assert first.audit.status is ProviderStatus.SUCCESS
    assert first.audit.cache_hit is False
    assert second.audit.cache_hit is True
    assert first.audit.response_hash == second.audit.response_hash


def test_cache_expiry_invokes_provider_again() -> None:
    clock = _MutableClock()
    backend = _ScriptedProvider([_market_response()])
    adapter = ProviderAdapter(backend, _policy(cache_ttl_seconds=1), clock=clock)
    request = {"structured_response": _market_response()}
    adapter.execute(request, MarketAnalysis, request_time_utc=DECISION_TIME)
    clock.value += timedelta(seconds=2)
    result = adapter.execute(request, MarketAnalysis, request_time_utc=DECISION_TIME)
    assert backend.calls == 2
    assert result.audit.cache_hit is False


def test_timeout_retries_are_bounded() -> None:
    backend = _ScriptedProvider([ProviderTimeoutError(), ProviderTimeoutError()])
    adapter = ProviderAdapter(backend, _policy(max_retry_attempts=1))
    result = adapter.execute({}, MarketAnalysis, request_time_utc=DECISION_TIME)
    assert result.audit.status is ProviderStatus.TIMEOUT
    assert result.audit.attempt_count == 2
    assert backend.calls == 2


def test_retry_backoff_is_configurable_without_real_sleep() -> None:
    delays: list[float] = []
    backend = _ScriptedProvider([ProviderTimeoutError(), _market_response()])
    adapter = ProviderAdapter(
        backend,
        _policy(max_retry_attempts=1, retry_backoff_seconds=0.25),
        sleeper=delays.append,
    )
    result = adapter.execute({}, MarketAnalysis, request_time_utc=DECISION_TIME)
    assert result.audit.status is ProviderStatus.SUCCESS
    assert result.audit.attempt_count == 2
    assert delays == [0.25]


def test_circuit_breaker_opens_and_cooldown_recovers() -> None:
    clock = _MutableClock()
    backend = _ScriptedProvider([ProviderTimeoutError(), _market_response()])
    adapter = ProviderAdapter(
        backend,
        _policy(max_retry_attempts=0, circuit_breaker_failure_threshold=1),
        clock=clock,
    )
    first = adapter.execute({}, MarketAnalysis, request_time_utc=DECISION_TIME)
    blocked = adapter.execute({}, MarketAnalysis, request_time_utc=DECISION_TIME)
    clock.value += timedelta(seconds=31)
    recovered = adapter.execute({}, MarketAnalysis, request_time_utc=DECISION_TIME)
    assert first.audit.status is ProviderStatus.TIMEOUT
    assert blocked.audit.status is ProviderStatus.CIRCUIT_OPEN
    assert recovered.audit.status is ProviderStatus.SUCCESS


def test_invalid_response_and_disabled_provider() -> None:
    invalid = ProviderAdapter(_ScriptedProvider([{"trend_strength": 0.1}]), _policy())
    invalid_result = invalid.execute({}, MarketAnalysis, request_time_utc=DECISION_TIME)
    disabled = ProviderAdapter(DeterministicOfflineProvider(enabled=False), _policy())
    disabled_result = disabled.execute({}, MarketAnalysis, request_time_utc=DECISION_TIME)
    assert invalid_result.audit.status is ProviderStatus.INVALID_RESPONSE
    assert disabled_result.audit.status is ProviderStatus.DISABLED


def test_rate_limited_provider_status_is_explicit() -> None:
    adapter = ProviderAdapter(
        _ScriptedProvider([ProviderRateLimitError()]),
        _policy(max_retry_attempts=0),
    )
    result = adapter.execute({}, MarketAnalysis, request_time_utc=DECISION_TIME)
    assert result.audit.status is ProviderStatus.RATE_LIMITED
    assert result.audit.attempt_count == 1


def test_one_invalid_agent_does_not_invent_context() -> None:
    invalid_market = _evidence("market")
    invalid_market["payload"]["momentum_score"] = 9.0
    request = CouncilRequest.model_validate(
        {
            "request_id": "one-invalid-agent",
            "symbol": "BTCUSDT",
            "decision_time_utc": DECISION_TIME.isoformat(),
            "evidence": [invalid_market, _evidence("microstructure"), _evidence("regime")],
        }
    )
    report = ResearchCouncilService(_config(min_valid_agents=3)).evaluate(
        request, project_root="."
    )
    assert report.status == "PARTIAL"
    assert report.snapshot is not None
    assert report.snapshot.market_context is None
    assert report.snapshot.agent_statuses["market_analyst_v1"].value == "INVALID_RESPONSE"


def test_high_disagreement_and_high_consensus_are_deterministic() -> None:
    bullish = _request("market", "microstructure", "regime")
    bullish_report = ResearchCouncilService(_config()).evaluate(bullish, project_root=".")
    mixed_raw = [
        _evidence("market"),
        _evidence(
            "microstructure",
            payload={**_payload("microstructure"), "flow_pressure": -1.0},
        ),
        _evidence(
            "regime",
            payload={**_payload("regime"), "regime_label": "trend_down"},
        ),
    ]
    mixed = CouncilRequest.model_validate(
        {
            "request_id": "mixed",
            "symbol": "BTCUSDT",
            "decision_time_utc": DECISION_TIME.isoformat(),
            "evidence": mixed_raw,
        }
    )
    mixed_report = ResearchCouncilService(_config()).evaluate(mixed, project_root=".")
    assert bullish_report.snapshot is not None and mixed_report.snapshot is not None
    assert bullish_report.snapshot.consensus_score > 0.0
    assert mixed_report.snapshot.disagreement_score > bullish_report.snapshot.disagreement_score


def test_explicit_write_only_materializes_allowed_research_outputs(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    request = _request("market", "microstructure", "regime")
    no_write = ResearchCouncilService(_config()).evaluate(request, project_root=project)
    assert no_write.write_performed is False
    assert not (project / "data").exists()
    written = ResearchCouncilService(_config()).evaluate(
        request, project_root=project, write_report=True
    )
    assert written.write_performed is True
    assert set(written.output_paths) == {"snapshot", "provider_audit"}
    assert all(path.startswith("data/") for path in written.output_paths.values())
    snapshot_path = project / written.output_paths["snapshot"]
    parsed = ContextIntelligenceSnapshot.model_validate_json(snapshot_path.read_text())
    assert parsed.snapshot_id == written.snapshot.snapshot_id if written.snapshot else False


def test_custom_output_outside_allowed_root_is_blocked(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    report = ResearchCouncilService(_config()).evaluate(
        _request("market", "microstructure", "regime"),
        project_root=project,
        write_report=True,
        output_json=project / "forbidden.json",
    )
    assert report.status == "BLOCKED"
    assert report.write_performed is False
    assert not (project / "forbidden.json").exists()


def test_contract_json_is_strictly_serializable() -> None:
    report = ResearchCouncilService(_config()).evaluate(
        _request("market", "microstructure", "regime"), project_root="."
    )
    rendered = json.dumps(report.model_dump(mode="json"), allow_nan=False, sort_keys=True)
    assert "NaN" not in rendered
    assert "operational_authority" in rendered
