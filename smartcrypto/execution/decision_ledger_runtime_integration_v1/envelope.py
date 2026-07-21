"""Attach immutable decision correlation to approved active signals."""

from __future__ import annotations

from typing import Any, Mapping

from smartcrypto.execution.decision_ledger_runtime_profile_v1 import RuntimeDecisionProjectionV1

from .contracts import ActiveSignalDecisionEnvelopeV1


class ActiveSignalEnvelopeError(ValueError):
    pass


def attach_decision_envelope(
    signal: Mapping[str, Any],
    projection: RuntimeDecisionProjectionV1,
) -> dict[str, Any]:
    target = projection.target_payload
    if signal.get("risk_approved") is not True:
        raise ActiveSignalEnvelopeError("active_signal_requires_risk_approved_true")
    if target.final_decision.value != "ALLOW":
        raise ActiveSignalEnvelopeError("active_signal_requires_final_allow")
    for field in ("pair", "symbol", "side", "signal_id", "candidate_id", "correlation_id"):
        left = str(signal.get(field))
        right_value = getattr(target, field)
        right = right_value.value if hasattr(right_value, "value") else str(right_value)
        if left != right:
            raise ActiveSignalEnvelopeError(f"signal_projection_mismatch:{field}")

    envelope = ActiveSignalDecisionEnvelopeV1(
        decision_event_id=target.event_id,
        decision_payload_sha256=target.payload_sha256,
        signal_id=target.signal_id,
        candidate_id=target.candidate_id,
        correlation_id=target.correlation_id,
        decision_timestamp=target.decision_timestamp,
    )
    result = dict(signal)
    result["decision_ledger"] = envelope.model_dump(mode="json")
    return result
