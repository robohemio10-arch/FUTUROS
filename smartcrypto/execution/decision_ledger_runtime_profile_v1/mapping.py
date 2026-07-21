"""Pure sandbox-only mapping adapters for decision and trade-link projections."""

from __future__ import annotations

from typing import Any

from smartcrypto.execution.decision_ledger_v4_2 import (
    DecisionRecordBodyV42,
    RiskDecision,
    TradeLinkRecordBodyV42,
    seal_decision_record,
    seal_trade_link_record,
)

from .contracts import (
    RuntimeDecisionInputV1,
    RuntimeDecisionLineageV1,
    RuntimeDecisionProjectionV1,
    RuntimeTradeLinkLineageV1,
    RuntimeTradeObservationInputV1,
    RuntimeTradeLinkProjectionV1,
)
from .field_sources import registry_sha256
from .identifiers import (
    canonical_mapping_sha256,
    decision_idempotency_key,
    event_id_from_idempotency_key,
    normalize_symbol,
    trade_link_idempotency_key,
)


def map_runtime_decision(
    source: RuntimeDecisionInputV1 | dict[str, Any],
) -> RuntimeDecisionProjectionV1:
    """Project authoritative inputs into a sealed design-only decision record."""

    validated = (
        source
        if isinstance(source, RuntimeDecisionInputV1)
        else RuntimeDecisionInputV1.model_validate(source)
    )

    expected_symbol = normalize_symbol(validated.pair)
    if validated.symbol != expected_symbol:
        raise ValueError(
            f"symbol_pair_mismatch:{validated.symbol}:{expected_symbol}"
        )

    mapping_source = validated.model_dump(mode="python")
    mapping_input_sha256 = canonical_mapping_sha256(mapping_source)
    idempotency_key = decision_idempotency_key(
        {
            "signal_id": validated.signal_id,
            "candidate_id": validated.candidate_id,
            "correlation_id": validated.correlation_id,
            "feature_timestamp": validated.feature_timestamp,
            "decision_timestamp": validated.decision_timestamp,
            "feature_hash": validated.feature_hash,
            "model_hash": validated.model_hash,
            "risk_config_hash": validated.risk_config_hash,
            "final_decision": validated.final_decision,
        }
    )
    event_id = event_id_from_idempotency_key(
        idempotency_key,
        prefix="decision-event",
    )

    risk_decision = (
        RiskDecision.APPROVED
        if validated.risk_approved
        else RiskDecision.REJECTED
    )

    body = DecisionRecordBodyV42(
        event_id=event_id,
        signal_id=validated.signal_id,
        candidate_id=validated.candidate_id,
        correlation_id=validated.correlation_id,
        idempotency_key=idempotency_key,
        runtime_mode=validated.runtime_mode,
        pair=validated.pair,
        symbol=validated.symbol,
        side=validated.side,
        feature_timestamp=validated.feature_timestamp,
        decision_timestamp=validated.decision_timestamp,
        feature_contract_version=validated.feature_contract_version,
        feature_hash=validated.feature_hash,
        model_id=validated.model_id,
        model_version=validated.model_version,
        model_hash=validated.model_hash,
        qlib_score=validated.qlib_score,
        calibrated_probability=validated.calibrated_probability,
        expected_net_pnl=validated.expected_net_pnl,
        fast_stop_probability=validated.fast_stop_probability,
        regime=validated.regime,
        alignment=validated.alignment,
        ai_shadow_decision=validated.ai_shadow_decision,
        ai_shadow_reasons=validated.ai_shadow_reasons,
        risk_decision=risk_decision,
        risk_reasons=validated.risk_reasons,
        approved_stake_usdt=validated.approved_stake_usdt,
        approved_leverage=validated.approved_leverage,
        final_decision=validated.final_decision,
        final_reasons=validated.final_reasons,
        operational_authority=False,
        runtime_integration=False,
        sends_orders=False,
        exchange_private_access=False,
    )
    sealed = seal_decision_record(body)

    lineage = RuntimeDecisionLineageV1(
        risk_checked_at_utc=validated.risk_checked_at_utc,
        risk_policy_id=validated.risk_policy_id,
        risk_config_hash=validated.risk_config_hash,
        source_signal_sha256=validated.source_signal_sha256,
        field_source_registry_sha256=registry_sha256(),
        mapping_input_sha256=mapping_input_sha256,
    )
    return RuntimeDecisionProjectionV1(
        lineage=lineage,
        target_payload=sealed,
    )


def map_runtime_trade_link(
    decision: RuntimeDecisionProjectionV1 | dict[str, Any],
    observation: RuntimeTradeObservationInputV1 | dict[str, Any],
) -> RuntimeTradeLinkProjectionV1:
    """Project an authoritative paper trade observation into append-only link."""

    decision_projection = (
        decision
        if isinstance(decision, RuntimeDecisionProjectionV1)
        else RuntimeDecisionProjectionV1.model_validate(decision)
    )
    validated = (
        observation
        if isinstance(observation, RuntimeTradeObservationInputV1)
        else RuntimeTradeObservationInputV1.model_validate(observation)
    )

    target = decision_projection.target_payload
    if validated.observed_pair != target.pair:
        raise ValueError("trade_pair_mismatch")
    if validated.observed_symbol != target.symbol:
        raise ValueError("trade_symbol_mismatch")
    if validated.observed_side is not target.side:
        raise ValueError("trade_side_mismatch")
    if validated.execution_timestamp < target.decision_timestamp:
        raise ValueError("trade_execution_before_decision")

    mapping_source = validated.model_dump(mode="python")
    mapping_input_sha256 = canonical_mapping_sha256(mapping_source)
    idempotency_key = trade_link_idempotency_key(
        decision_event_id=target.event_id,
        trade_id=validated.trade_id,
        source_row_fingerprint=validated.source_row_fingerprint,
    )
    event_id = event_id_from_idempotency_key(
        idempotency_key,
        prefix="trade-link-event",
    )

    body = TradeLinkRecordBodyV42(
        event_id=event_id,
        parent_event_id=target.event_id,
        signal_id=target.signal_id,
        candidate_id=target.candidate_id,
        trade_id=validated.trade_id,
        correlation_id=target.correlation_id,
        idempotency_key=idempotency_key,
        runtime_mode=target.runtime_mode,
        pair=target.pair,
        symbol=target.symbol,
        side=target.side,
        decision_timestamp=target.decision_timestamp,
        execution_timestamp=validated.execution_timestamp,
        decision_payload_sha256=target.payload_sha256,
        link_reason=validated.link_reason,
        operational_authority=False,
        runtime_integration=False,
        sends_orders=False,
        exchange_private_access=False,
    )
    sealed = seal_trade_link_record(body)

    lineage = RuntimeTradeLinkLineageV1(
        source_database_sha256=validated.source_database_sha256,
        source_table=validated.source_table,
        source_row_fingerprint=validated.source_row_fingerprint,
        field_source_registry_sha256=registry_sha256(),
        mapping_input_sha256=mapping_input_sha256,
    )
    return RuntimeTradeLinkProjectionV1(
        lineage=lineage,
        target_payload=sealed,
    )
