from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from smartcrypto.execution.paper_candidate_trade_lineage_propagation_v1 import (
    AuthoritativeCandidateSignalIdentityV1,
    ConcreteSignalIdentityBindingV1,
    ConcreteSignalOccurrenceV1,
    ResearchSignalCandidateReferenceV1,
    CandidateLineageError,
    StrictDecisionProjectionV1,
    StrictTradeLinkProjectionV1,
    build_authoritative_signal_identity,
    build_research_candidate_reference,
    materialize_concrete_signal_identity,
    project_strict_decision,
    project_strict_trade_link,
)


def authoritative_signal() -> dict[str, object]:
    return {
        "candidate_id": "candidate-alpha",
        "signal_id": "signal-alpha-20260820T120000Z",
        "correlation_id": "correlation-alpha-20260820T120000Z",
        "pair": "BTC/USDT:USDT",
        "symbol": "BTCUSDT",
        "side": "long",
        "feature_timestamp": "2026-08-20T12:00:00Z",
        "model_version": "v1",
        "score": 0.25,
    }


def identity() -> AuthoritativeCandidateSignalIdentityV1:
    return build_authoritative_signal_identity(authoritative_signal())


def decision_fields() -> dict[str, object]:
    return {
        "runtime_mode": "paper",
        "pair": "BTC/USDT:USDT",
        "symbol": "BTCUSDT",
        "side": "long",
        "feature_timestamp": "2026-08-20T12:00:00Z",
        "decision_timestamp": "2026-08-20T12:00:02Z",
        "risk_checked_at_utc": "2026-08-20T12:00:01Z",
        "feature_contract_version": "market-feature-contract-v1",
        "feature_hash": "2" * 64,
        "model_id": "qlib-ranking",
        "model_version": "v1",
        "model_hash": "3" * 64,
        "qlib_score": 0.25,
        "calibrated_probability": 0.60,
        "expected_net_pnl": 0.12,
        "fast_stop_probability": 0.10,
        "regime": "trend",
        "alignment": "aligned",
        "ai_shadow_decision": "ALLOW",
        "ai_shadow_reasons": [],
        "risk_approved": True,
        "risk_reasons": [],
        "risk_policy_id": "paper-risk-policy-v1",
        "risk_config_hash": "4" * 64,
        "approved_stake_usdt": 100.0,
        "approved_leverage": 2.0,
        "final_decision": "ALLOW",
        "final_reasons": ["risk_manager_approved"],
        "source_signal_sha256": "5" * 64,
        "operational_authority": False,
        "runtime_integration": False,
        "sends_orders": False,
        "exchange_private_access": False,
    }


def trade_observation() -> dict[str, object]:
    return {
        "trade_id": 123,
        "execution_timestamp": "2026-08-20T12:00:03Z",
        "observed_pair": "BTC/USDT:USDT",
        "observed_symbol": "BTCUSDT",
        "observed_side": "long",
        "source_database_sha256": "6" * 64,
        "source_table": "trades",
        "source_row_fingerprint": "7" * 64,
        "link_reason": "explicit_decision_event_id_in_enter_tag",
        "operational_authority": False,
        "runtime_integration": False,
        "sends_orders": False,
        "exchange_private_access": False,
    }


def strict_decision() -> StrictDecisionProjectionV1:
    return project_strict_decision(identity(), decision_fields())


def test_exact_signal_identity_fields_are_preserved() -> None:
    value = identity()
    assert value.candidate_id == "candidate-alpha"
    assert value.signal_id == "signal-alpha-20260820T120000Z"
    assert value.correlation_id == "correlation-alpha-20260820T120000Z"
    assert value.candidate_id_origin == "candidate_id"
    assert value.signal_id_origin == "signal_id"
    assert value.correlation_id_origin == "correlation_id"
    assert value.synthetic_candidate_id is False
    assert value.synthetic_signal_id is False
    assert value.synthetic_correlation_id is False
    assert value.trade_id_used_as_candidate_id is False


def test_signal_identity_is_deterministic_for_same_source_payload() -> None:
    first = identity()
    second = identity()
    assert first == second
    assert len(first.source_signal_sha256) == 64


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("candidate_id", "authoritative_candidate_id_missing"),
        ("signal_id", "authoritative_signal_id_missing"),
        ("correlation_id", "authoritative_correlation_id_missing"),
    ],
)
def test_missing_exact_identity_field_fails_closed(
    field: str,
    reason: str,
) -> None:
    source = authoritative_signal()
    source.pop(field)

    with pytest.raises(CandidateLineageError, match=reason):
        build_authoritative_signal_identity(source)


def test_research_aliases_are_not_accepted_as_runtime_identity_fallback() -> None:
    source = authoritative_signal()
    source.pop("candidate_id")
    source.pop("signal_id")
    source["source_candidate_id"] = "candidate-alpha"
    source["signal_candidate_id"] = "signal-candidate-alpha"

    with pytest.raises(
        CandidateLineageError,
        match="authoritative_candidate_id_missing",
    ):
        build_authoritative_signal_identity(source)


def test_blank_identity_fails_closed() -> None:
    source = authoritative_signal()
    source["candidate_id"] = " "

    with pytest.raises(
        CandidateLineageError,
        match="authoritative_candidate_id_missing",
    ):
        build_authoritative_signal_identity(source)


def test_invalid_identifier_fails_closed() -> None:
    source = authoritative_signal()
    source["signal_id"] = "invalid signal with spaces"

    with pytest.raises(
        CandidateLineageError,
        match="authoritative_identity_contract_invalid",
    ):
        build_authoritative_signal_identity(source)


def test_strict_decision_preserves_exact_authoritative_identity() -> None:
    projected = strict_decision()
    target = projected.decision_projection.target_payload

    assert target.candidate_id == "candidate-alpha"
    assert target.signal_id == "signal-alpha-20260820T120000Z"
    assert target.correlation_id == "correlation-alpha-20260820T120000Z"
    assert target.trade_id is None


def test_strict_decision_is_deterministic() -> None:
    assert strict_decision() == strict_decision()


@pytest.mark.parametrize(
    "field",
    ["candidate_id", "signal_id", "correlation_id"],
)
def test_decision_identity_override_is_forbidden(field: str) -> None:
    fields = decision_fields()
    fields[field] = "override"

    with pytest.raises(
        CandidateLineageError,
        match="decision_identity_override_forbidden",
    ):
        project_strict_decision(identity(), fields)


def test_trade_id_is_forbidden_in_decision_lineage() -> None:
    fields = decision_fields()
    fields["trade_id"] = 123

    with pytest.raises(
        CandidateLineageError,
        match="trade_id_forbidden_in_decision_lineage",
    ):
        project_strict_decision(identity(), fields)


def test_strict_decision_safety_is_isolated() -> None:
    safety = strict_decision().safety_flags

    assert safety.prospective_only is True
    assert safety.historical_backfill_allowed is False
    assert safety.fuzzy_linkage_allowed is False
    assert safety.timestamp_only_matching_allowed is False
    assert safety.symbol_side_only_matching_allowed is False
    assert safety.trade_id_as_candidate_id_allowed is False
    assert safety.synthetic_candidate_id_allowed is False
    assert safety.synthetic_signal_id_allowed is False
    assert safety.synthetic_correlation_id_allowed is False
    assert safety.publisher_touched is False
    assert safety.writer_invoked is False
    assert safety.writes_runtime is False
    assert safety.changes_risk is False
    assert safety.sends_orders is False


def test_trade_link_preserves_identity_and_parent_decision() -> None:
    decision = strict_decision()
    linked = project_strict_trade_link(decision, trade_observation())
    target = linked.trade_link_projection.target_payload

    assert isinstance(linked, StrictTradeLinkProjectionV1)
    assert target.candidate_id == decision.identity.candidate_id
    assert target.signal_id == decision.identity.signal_id
    assert target.correlation_id == decision.identity.correlation_id
    assert target.parent_event_id == (
        decision.decision_projection.target_payload.event_id
    )
    assert target.trade_id == 123


def test_trade_link_is_deterministic_and_idempotency_key_is_stable() -> None:
    decision = strict_decision()
    first = project_strict_trade_link(decision, trade_observation())
    second = project_strict_trade_link(decision, trade_observation())

    assert first == second
    assert (
        first.trade_link_projection.target_payload.idempotency_key
        == second.trade_link_projection.target_payload.idempotency_key
    )


@pytest.mark.parametrize(
    "field",
    [
        "candidate_id",
        "signal_id",
        "correlation_id",
        "decision_event_id",
        "parent_event_id",
    ],
)
def test_trade_observation_cannot_override_identity(field: str) -> None:
    observation = trade_observation()
    observation[field] = "override"

    with pytest.raises(
        CandidateLineageError,
        match="trade_observation_identity_override_forbidden",
    ):
        project_strict_trade_link(strict_decision(), observation)


def test_trade_pair_mismatch_fails_closed() -> None:
    observation = trade_observation()
    observation["observed_pair"] = "ETH/USDT:USDT"

    with pytest.raises(
        CandidateLineageError,
        match="strict_trade_link_projection_failed",
    ):
        project_strict_trade_link(strict_decision(), observation)


def test_projection_contracts_are_immutable() -> None:
    projected = strict_decision()

    with pytest.raises(ValidationError):
        projected.identity.candidate_id = "changed"  # type: ignore[misc]


def test_mapper_has_no_runtime_writer_network_sqlite_or_exchange_imports() -> None:
    module_path = (
        Path(__file__).resolve().parents[1]
        / "smartcrypto"
        / "execution"
        / "paper_candidate_trade_lineage_propagation_v1"
        / "mapper.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imports: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module or "")

    forbidden = {
        "sqlite3",
        "ccxt",
        "requests",
        "httpx",
        "aiohttp",
        "redis",
        "freqtrade",
    }
    assert not any(
        import_name == item or import_name.startswith(f"{item}.")
        for import_name in imports
        for item in forbidden
    )
    assert not any("paper_runtime_writer" in item for item in imports)


def test_mapper_contains_no_identity_fallback_or_generation() -> None:
    module_path = (
        Path(__file__).resolve().parents[1]
        / "smartcrypto"
        / "execution"
        / "paper_candidate_trade_lineage_propagation_v1"
        / "mapper.py"
    )
    source = module_path.read_text(encoding="utf-8")

    assert 'setdefault("candidate_id"' not in source
    assert 'setdefault("signal_id"' not in source
    assert 'source.get("source_candidate_id")' not in source
    assert 'source.get("signal_candidate_id")' not in source


# ---------------------------------------------------------------------------
# Stage 2: strict research-candidate -> concrete-signal identity materializer
# ---------------------------------------------------------------------------


def _signal_candidate_id(
    source_candidate_id: object,
    source_model_candidate_type: object,
    source_id: object,
    threshold: object,
    signal_actionability: object,
) -> str:
    digest = hashlib.sha256(
        "|".join(
            str(item)
            for item in (
                source_candidate_id,
                source_model_candidate_type,
                source_id,
                threshold,
                signal_actionability,
            )
        ).encode("utf-8")
    ).hexdigest()
    return f"signal_candidate_{digest[:16]}"


def research_candidate(
    *,
    source_candidate_id: str = "registry-candidate-real-alpha",
    actionability: str = "research_observation_only",
) -> dict[str, object]:
    row: dict[str, object] = {
        "source_candidate_id": source_candidate_id,
        "source_model_candidate_type": "qlib_ranking_candidate",
        "source_id": "qlib-ranking:v1",
        "symbol_scope": ["BTCUSDT"],
        "side_scope": ["long"],
        "regime_scope": ["trend"],
        "threshold": 0.61,
        "ensemble_score_summary": {"auc": 0.58, "brier": 0.22},
        "signal_direction": "long",
        "signal_confidence": 0.61,
        "evidence_status": "research_only",
        "signal_actionability": actionability,
        "blocked_reasons": [],
        "eligible_for_research_observation": actionability == "research_observation_only",
        "eligible_for_paper_selector": False,
        "eligible_for_freqtrade": False,
        "operational_authority": False,
        "sends_orders": False,
        "writes_runtime": False,
        "updates_freqtrade": False,
    }
    row["signal_candidate_id"] = _signal_candidate_id(
        row["source_candidate_id"],
        row["source_model_candidate_type"],
        row["source_id"],
        row["threshold"],
        row["signal_actionability"],
    )
    return row


def registry_candidate(
    *,
    candidate_id: str = "registry-candidate-real-alpha",
) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "candidate_type": "qlib_ranking_candidate",
        "source_id": "qlib-ranking:v1",
        "gate_status": "eligible_for_research_review",
        "threshold": 0.61,
        "symbol_scope": ["BTCUSDT"],
        "side_scope": ["long"],
        "regime_scope": ["trend"],
    }


def occurrence(
    *,
    signal_instance_id: str = "signal-instance-20260821-000001",
    symbol: str = "BTCUSDT",
    pair: str = "BTC/USDT:USDT",
    side: str = "long",
    regime: str = "trend",
    occurrence_source_sha256: str = "8" * 64,
) -> dict[str, object]:
    return {
        "producer_id": "paper-signal-materializer-v1",
        "signal_instance_id": signal_instance_id,
        "signal_timestamp_utc": "2026-08-21T14:00:00Z",
        "pair": pair,
        "symbol": symbol,
        "side": side,
        "regime": regime,
        "occurrence_source_sha256": occurrence_source_sha256,
        "operational_authority": False,
        "runtime_integration": False,
        "sends_orders": False,
        "exchange_private_access": False,
    }


def research_reference() -> ResearchSignalCandidateReferenceV1:
    return build_research_candidate_reference(
        research_candidate(),
        registry_candidate(),
        producer_id="paper-signal-materializer-v1",
    )


def concrete_binding() -> ConcreteSignalIdentityBindingV1:
    return materialize_concrete_signal_identity(
        research_candidate(),
        registry_candidate(),
        occurrence(),
        producer_id="paper-signal-materializer-v1",
    )


def test_stage2_reference_proves_registry_candidate_identity() -> None:
    reference = research_reference()

    assert reference.source_candidate_id == "registry-candidate-real-alpha"
    assert reference.registry_candidate_id == "registry-candidate-real-alpha"
    assert reference.registry_candidate_identity_verified is True
    assert reference.signal_candidate_id_integrity_verified is True
    assert reference.runtime_candidate_id_materialized is False
    assert reference.runtime_signal_id_materialized is False


def test_stage2_reference_is_deterministic() -> None:
    assert research_reference() == research_reference()


def test_stage2_registry_candidate_mismatch_fails_closed() -> None:
    with pytest.raises(
        CandidateLineageError,
        match="research_candidate_registry_identity_mismatch",
    ):
        build_research_candidate_reference(
            research_candidate(),
            registry_candidate(candidate_id="different-candidate"),
            producer_id="paper-signal-materializer-v1",
        )


def test_stage2_legacy_registry_fallback_is_rejected() -> None:
    candidate = research_candidate(source_candidate_id="registry-candidate-12")
    registry = registry_candidate(candidate_id="registry-candidate-12")

    with pytest.raises(
        CandidateLineageError,
        match="legacy_registry_candidate_fallback_not_authoritative",
    ):
        build_research_candidate_reference(
            candidate,
            registry,
            producer_id="paper-signal-materializer-v1",
        )


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("source_candidate_id", "research_source_candidate_id_missing"),
        ("signal_candidate_id", "research_signal_candidate_id_missing"),
        ("signal_actionability", "research_signal_actionability_missing"),
    ],
)
def test_stage2_required_research_identity_fields_fail_closed(
    field: str,
    reason: str,
) -> None:
    candidate = research_candidate()
    candidate.pop(field)

    with pytest.raises(CandidateLineageError, match=reason):
        build_research_candidate_reference(
            candidate,
            registry_candidate(),
            producer_id="paper-signal-materializer-v1",
        )


def test_stage2_registry_candidate_id_is_required() -> None:
    registry = registry_candidate()
    registry.pop("candidate_id")

    with pytest.raises(CandidateLineageError, match="registry_candidate_id_missing"):
        build_research_candidate_reference(
            research_candidate(),
            registry,
            producer_id="paper-signal-materializer-v1",
        )


def test_stage2_tampered_signal_candidate_id_fails_integrity_check() -> None:
    candidate = research_candidate()
    candidate["signal_candidate_id"] = "signal_candidate_tampered"

    with pytest.raises(
        CandidateLineageError,
        match="research_signal_candidate_id_integrity_mismatch",
    ):
        build_research_candidate_reference(
            candidate,
            registry_candidate(),
            producer_id="paper-signal-materializer-v1",
        )


def test_stage2_post_outcome_field_is_rejected() -> None:
    candidate = research_candidate()
    candidate["realized_pnl_usdt"] = 12.0

    with pytest.raises(
        CandidateLineageError,
        match="research_candidate_contains_post_outcome_field:realized_pnl_usdt",
    ):
        build_research_candidate_reference(
            candidate,
            registry_candidate(),
            producer_id="paper-signal-materializer-v1",
        )


def test_stage2_blocked_research_candidate_cannot_materialize_signal() -> None:
    candidate = research_candidate(actionability="blocked")
    candidate["signal_candidate_id"] = _signal_candidate_id(
        candidate["source_candidate_id"],
        candidate["source_model_candidate_type"],
        candidate["source_id"],
        candidate["threshold"],
        candidate["signal_actionability"],
    )

    reference = build_research_candidate_reference(
        candidate,
        registry_candidate(),
        producer_id="paper-signal-materializer-v1",
    )
    assert reference.signal_actionability == "blocked"

    with pytest.raises(
        CandidateLineageError,
        match="research_candidate_not_materialization_eligible:blocked",
    ):
        materialize_concrete_signal_identity(
            candidate,
            registry_candidate(),
            occurrence(),
            producer_id="paper-signal-materializer-v1",
        )


def test_stage2_materialization_propagates_candidate_id_unchanged() -> None:
    binding = concrete_binding()

    assert binding.identity.candidate_id == "registry-candidate-real-alpha"
    assert binding.research_reference.source_candidate_id == binding.identity.candidate_id
    assert binding.candidate_id_propagated_unchanged is True
    assert binding.identity.synthetic_candidate_id is False


def test_stage2_materialization_creates_real_per_signal_identity() -> None:
    binding = concrete_binding()

    assert binding.identity.signal_id.startswith("signal:")
    assert binding.identity.correlation_id.startswith("correlation:")
    assert binding.identity.signal_id != binding.research_reference.signal_candidate_id
    assert binding.signal_candidate_id_reused_as_signal_id is False
    assert binding.random_identity_used is False


def test_stage2_materialization_is_deterministic() -> None:
    first = concrete_binding()
    second = concrete_binding()

    assert first == second
    assert first.materialization_sha256 == second.materialization_sha256


def test_stage2_different_signal_instance_produces_different_signal_identity() -> None:
    first = concrete_binding()
    second = materialize_concrete_signal_identity(
        research_candidate(),
        registry_candidate(),
        occurrence(signal_instance_id="signal-instance-20260821-000002"),
        producer_id="paper-signal-materializer-v1",
    )

    assert first.identity.candidate_id == second.identity.candidate_id
    assert first.identity.signal_id != second.identity.signal_id
    assert first.identity.correlation_id != second.identity.correlation_id


def test_stage2_occurrence_fingerprint_is_part_of_signal_identity() -> None:
    first = concrete_binding()
    second = materialize_concrete_signal_identity(
        research_candidate(),
        registry_candidate(),
        occurrence(occurrence_source_sha256="9" * 64),
        producer_id="paper-signal-materializer-v1",
    )

    assert first.identity.signal_id != second.identity.signal_id


def test_stage2_occurrence_requires_explicit_signal_instance_id() -> None:
    value = occurrence()
    value.pop("signal_instance_id")

    with pytest.raises(
        CandidateLineageError,
        match="concrete_signal_occurrence_contract_invalid",
    ):
        materialize_concrete_signal_identity(
            research_candidate(),
            registry_candidate(),
            value,
            producer_id="paper-signal-materializer-v1",
        )


def test_stage2_occurrence_requires_utc_timestamp() -> None:
    value = occurrence()
    value["signal_timestamp_utc"] = "2026-08-21T14:00:00-03:00"

    with pytest.raises(
        CandidateLineageError,
        match="concrete_signal_occurrence_contract_invalid",
    ):
        materialize_concrete_signal_identity(
            research_candidate(),
            registry_candidate(),
            value,
            producer_id="paper-signal-materializer-v1",
        )


def test_stage2_producer_mismatch_fails_closed() -> None:
    value = occurrence()
    value["producer_id"] = "different-producer"

    with pytest.raises(
        CandidateLineageError,
        match="materialization_producer_id_mismatch",
    ):
        materialize_concrete_signal_identity(
            research_candidate(),
            registry_candidate(),
            value,
            producer_id="paper-signal-materializer-v1",
        )


def test_stage2_pair_symbol_mismatch_fails_closed() -> None:
    with pytest.raises(
        CandidateLineageError,
        match="concrete_signal_symbol_pair_mismatch",
    ):
        materialize_concrete_signal_identity(
            research_candidate(),
            registry_candidate(),
            occurrence(symbol="ETHUSDT"),
            producer_id="paper-signal-materializer-v1",
        )


def test_stage2_symbol_outside_research_scope_fails_closed() -> None:
    candidate = research_candidate()
    candidate["symbol_scope"] = ["ETHUSDT"]

    with pytest.raises(
        CandidateLineageError,
        match="concrete_signal_symbol_outside_research_scope",
    ):
        materialize_concrete_signal_identity(
            candidate,
            registry_candidate(),
            occurrence(),
            producer_id="paper-signal-materializer-v1",
        )


def test_stage2_side_outside_research_scope_fails_closed() -> None:
    candidate = research_candidate()
    candidate["side_scope"] = ["short"]

    with pytest.raises(
        CandidateLineageError,
        match="concrete_signal_side_outside_research_scope",
    ):
        materialize_concrete_signal_identity(
            candidate,
            registry_candidate(),
            occurrence(),
            producer_id="paper-signal-materializer-v1",
        )


def test_stage2_regime_outside_research_scope_fails_closed() -> None:
    with pytest.raises(
        CandidateLineageError,
        match="concrete_signal_regime_outside_research_scope",
    ):
        materialize_concrete_signal_identity(
            research_candidate(),
            registry_candidate(),
            occurrence(regime="range"),
            producer_id="paper-signal-materializer-v1",
        )


def test_stage2_signal_direction_mismatch_fails_closed() -> None:
    candidate = research_candidate()
    candidate["signal_direction"] = "short"

    with pytest.raises(
        CandidateLineageError,
        match="concrete_signal_direction_mismatch",
    ):
        materialize_concrete_signal_identity(
            candidate,
            registry_candidate(),
            occurrence(),
            producer_id="paper-signal-materializer-v1",
        )


def test_stage2_binding_is_immutable_and_isolated() -> None:
    binding = concrete_binding()
    safety = binding.safety_flags

    assert safety.prospective_only is True
    assert safety.registry_candidate_proof_required is True
    assert safety.explicit_signal_instance_key_required is True
    assert safety.fallback_identity_generation_allowed is False
    assert safety.post_outcome_identity_inputs_allowed is False
    assert safety.publisher_touched is False
    assert safety.writer_invoked is False
    assert safety.writes_runtime is False
    assert safety.changes_risk is False
    assert safety.sends_orders is False

    with pytest.raises(ValidationError):
        binding.identity.signal_id = "changed"  # type: ignore[misc]


def test_stage2_adapter_has_no_runtime_writer_publisher_or_network_imports() -> None:
    module_path = (
        Path(__file__).resolve().parents[1]
        / "smartcrypto"
        / "execution"
        / "paper_candidate_trade_lineage_propagation_v1"
        / "adapter.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imports: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module or "")

    forbidden = {
        "sqlite3",
        "ccxt",
        "requests",
        "httpx",
        "aiohttp",
        "redis",
        "freqtrade",
        "random",
        "uuid",
    }
    assert not any(
        import_name == item or import_name.startswith(f"{item}.")
        for import_name in imports
        for item in forbidden
    )
    assert not any("paper_runtime_writer" in item for item in imports)
    assert not any("paper_observability_wiring" in item for item in imports)


def test_stage2_adapter_contains_no_wall_clock_or_random_identity_generation() -> None:
    module_path = (
        Path(__file__).resolve().parents[1]
        / "smartcrypto"
        / "execution"
        / "paper_candidate_trade_lineage_propagation_v1"
        / "adapter.py"
    )
    source = module_path.read_text(encoding="utf-8")

    assert "datetime.now" not in source
    assert "time.time" not in source
    assert "uuid4" not in source
    assert "random." not in source
    assert 'source.get("trade_id")' not in source
    assert "timestamp_only" not in source
