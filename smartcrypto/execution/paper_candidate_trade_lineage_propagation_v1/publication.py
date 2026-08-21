"""Non-blocking Paper publication boundary for prospective lineage.

Stage 3A is intentionally pure: it does not write runtime state, does not call
Freqtrade, does not call RiskManager, does not invoke the Decision Ledger
writer, and does not submit orders.

The contract is asymmetric by design:

* RiskManager-approved Paper execution is the operational baseline.
* Lineage is optional metadata/evidence.
* A lineage/observability failure blocks attribution evidence only.
* A lineage/observability failure MUST NOT turn a RiskManager ALLOW into a
  publication BLOCK.
* Only a verified ``decision_ledger`` envelope may be added to an approved
  signal. No other observability-produced fields are published by this module.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal

from .contracts import ConcreteSignalIdentityBindingV1
from .mapper import CandidateLineageError


ATTESTATION_KEY = "paper_candidate_trade_lineage"
DECISION_LEDGER_KEY = "decision_ledger"
ATTESTATION_SCHEMA = "paper_candidate_trade_lineage_signal_attestation_v1"
PUBLICATION_SCHEMA = "paper_candidate_trade_lineage_non_blocking_publication_v1"


@dataclass(frozen=True)
class PaperLineagePublicationResultV1:
    """Pure result of the non-blocking publication decision."""

    active_signals: tuple[dict[str, Any], ...]
    status: Literal[
        "empty",
        "baseline_preserved",
        "lineage_propagated",
        "blocked",
    ]
    reason: str
    baseline_approved_count: int
    observability_active_count: int
    published_signal_count: int
    lineage_envelope_count: int
    attribution_evidence_blocked: bool
    baseline_execution_preserved: bool
    risk_decision_preserved: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PUBLICATION_SCHEMA,
            "status": self.status,
            "reason": self.reason,
            "baseline_approved_count": self.baseline_approved_count,
            "observability_active_count": self.observability_active_count,
            "published_signal_count": self.published_signal_count,
            "lineage_envelope_count": self.lineage_envelope_count,
            "attribution_evidence_blocked": self.attribution_evidence_blocked,
            "baseline_execution_preserved": self.baseline_execution_preserved,
            "risk_decision_preserved": self.risk_decision_preserved,
            "publication_blocked_by_lineage": False,
            "partial_lineage_publication_allowed": False,
            "synthetic_lineage_publication_allowed": False,
            "fuzzy_matching_allowed": False,
            "timestamp_only_matching_allowed": False,
            "trade_id_as_candidate_id_allowed": False,
            "writer_invoked": False,
            "writes_runtime": False,
            "writes_sqlite": False,
            "changes_risk": False,
            "changes_strategy": False,
            "sends_orders": False,
            "exchange_private_access": False,
            "live_release_allowed": False,
            "canary_release_allowed": False,
        }


def attach_materialized_identity_to_signal(
    signal: Mapping[str, Any],
    binding: ConcreteSignalIdentityBindingV1,
) -> dict[str, Any]:
    """Attach a Stage-2 verified identity to one concrete pre-execution signal.

    This is a pure materialization helper. It refuses to overwrite conflicting
    identity fields and refuses pair/symbol/side drift relative to the explicit
    Stage-2 occurrence.
    """

    if not isinstance(signal, Mapping):
        raise CandidateLineageError("concrete_signal_must_be_mapping")

    source = dict(signal)
    occurrence = binding.occurrence
    identity = binding.identity

    _require_equal_text(
        source.get("pair"),
        occurrence.pair,
        "concrete_signal_pair_binding_mismatch",
    )
    _require_equal_text(
        source.get("symbol"),
        occurrence.symbol,
        "concrete_signal_symbol_binding_mismatch",
    )
    _require_equal_text(
        str(source.get("side") or "").lower(),
        occurrence.side,
        "concrete_signal_side_binding_mismatch",
    )

    for field, expected in (
        ("candidate_id", identity.candidate_id),
        ("signal_id", identity.signal_id),
        ("correlation_id", identity.correlation_id),
    ):
        existing = source.get(field)
        if existing is not None and str(existing).strip() != expected:
            raise CandidateLineageError(f"concrete_signal_identity_conflict:{field}")

    existing_attestation = source.get(ATTESTATION_KEY)
    if existing_attestation is not None:
        raise CandidateLineageError("concrete_signal_lineage_attestation_already_present")

    enriched = dict(source)
    enriched["candidate_id"] = identity.candidate_id
    enriched["signal_id"] = identity.signal_id
    enriched["correlation_id"] = identity.correlation_id
    enriched[ATTESTATION_KEY] = {
        "schema_version": ATTESTATION_SCHEMA,
        "materialization_sha256": binding.materialization_sha256,
        "source_signal_sha256": identity.source_signal_sha256,
        "research_candidate_sha256": binding.research_reference.research_candidate_sha256,
        "registry_candidate_sha256": binding.research_reference.registry_candidate_sha256,
        "candidate_id": identity.candidate_id,
        "signal_id": identity.signal_id,
        "correlation_id": identity.correlation_id,
        "research_signal_candidate_id": binding.research_reference.signal_candidate_id,
        "signal_instance_id": occurrence.signal_instance_id,
        "producer_id": occurrence.producer_id,
        "prospective_only": True,
        "authoritative_identity": True,
        "synthetic_identity": False,
        "trade_id_used_as_candidate_id": False,
    }
    return enriched


def select_non_blocking_paper_publication_signals(
    *,
    risk_gate: Any,
    observability: Any,
) -> PaperLineagePublicationResultV1:
    """Choose Paper publication payload without granting lineage veto power.

    ``risk_gate.approved_signals`` is the immutable execution baseline.
    Observability may contribute only a validated ``decision_ledger`` envelope.
    Any lineage failure returns the baseline approved signals unchanged.
    """

    risk_status = str(getattr(risk_gate, "status", "") or "")
    baseline = tuple(
        dict(item)
        for item in _mapping_sequence(getattr(risk_gate, "approved_signals", ()))
    )

    if risk_status != "ok":
        return PaperLineagePublicationResultV1(
            active_signals=(),
            status="blocked",
            reason="risk_gate_not_ok",
            baseline_approved_count=len(baseline),
            observability_active_count=0,
            published_signal_count=0,
            lineage_envelope_count=0,
            attribution_evidence_blocked=True,
            baseline_execution_preserved=True,
            risk_decision_preserved=True,
        )

    if not baseline:
        return PaperLineagePublicationResultV1(
            active_signals=(),
            status="empty",
            reason="no_risk_approved_signals",
            baseline_approved_count=0,
            observability_active_count=0,
            published_signal_count=0,
            lineage_envelope_count=0,
            attribution_evidence_blocked=False,
            baseline_execution_preserved=True,
            risk_decision_preserved=True,
        )

    for signal in baseline:
        if signal.get("risk_approved") is not True:
            return PaperLineagePublicationResultV1(
                active_signals=(),
                status="blocked",
                reason="risk_gate_approved_signal_missing_true_approval",
                baseline_approved_count=len(baseline),
                observability_active_count=0,
                published_signal_count=0,
                lineage_envelope_count=0,
                attribution_evidence_blocked=True,
                baseline_execution_preserved=False,
                risk_decision_preserved=False,
            )

    report = getattr(observability, "report", None)
    publication_blocked = bool(getattr(report, "publication_blocked", False))
    observability_signals = tuple(
        dict(item)
        for item in _mapping_sequence(getattr(observability, "active_signals", ()))
    )

    if publication_blocked:
        return _baseline_result(
            baseline,
            observability_signals,
            reason="lineage_observability_blocked_baseline_preserved",
        )

    if len(observability_signals) != len(baseline):
        return _baseline_result(
            baseline,
            observability_signals,
            reason="lineage_observability_count_mismatch_baseline_preserved",
        )

    published: list[dict[str, Any]] = []
    for index, (approved, observed) in enumerate(
        zip(baseline, observability_signals, strict=True)
    ):
        failure = _validate_observed_signal(
            approved=approved,
            observed=observed,
            index=index,
        )
        if failure is not None:
            return _baseline_result(
                baseline,
                observability_signals,
                reason=failure,
            )

        envelope = deepcopy(observed[DECISION_LEDGER_KEY])
        output = dict(approved)
        output[DECISION_LEDGER_KEY] = envelope
        published.append(output)

    return PaperLineagePublicationResultV1(
        active_signals=tuple(published),
        status="lineage_propagated",
        reason="strict_decision_ledger_envelope_propagated",
        baseline_approved_count=len(baseline),
        observability_active_count=len(observability_signals),
        published_signal_count=len(published),
        lineage_envelope_count=len(published),
        attribution_evidence_blocked=False,
        baseline_execution_preserved=True,
        risk_decision_preserved=True,
    )


def _baseline_result(
    baseline: Sequence[Mapping[str, Any]],
    observability_signals: Sequence[Mapping[str, Any]],
    *,
    reason: str,
) -> PaperLineagePublicationResultV1:
    copied = tuple(dict(item) for item in baseline)
    return PaperLineagePublicationResultV1(
        active_signals=copied,
        status="baseline_preserved",
        reason=reason,
        baseline_approved_count=len(copied),
        observability_active_count=len(observability_signals),
        published_signal_count=len(copied),
        lineage_envelope_count=0,
        attribution_evidence_blocked=True,
        baseline_execution_preserved=True,
        risk_decision_preserved=True,
    )


def _validate_observed_signal(
    *,
    approved: Mapping[str, Any],
    observed: Mapping[str, Any],
    index: int,
) -> str | None:
    # Observability is not allowed to change anything RiskManager approved.
    for key, expected in approved.items():
        if key not in observed:
            return f"lineage_observability_removed_baseline_field:{index}:{key}"
        if observed.get(key) != expected:
            return f"lineage_observability_changed_baseline_field:{index}:{key}"

    attestation = approved.get(ATTESTATION_KEY)
    if not isinstance(attestation, Mapping):
        return f"lineage_attestation_missing:{index}"
    if attestation.get("schema_version") != ATTESTATION_SCHEMA:
        return f"lineage_attestation_schema_invalid:{index}"
    if attestation.get("prospective_only") is not True:
        return f"lineage_attestation_not_prospective:{index}"
    if attestation.get("authoritative_identity") is not True:
        return f"lineage_attestation_not_authoritative:{index}"
    if attestation.get("synthetic_identity") is not False:
        return f"lineage_attestation_synthetic:{index}"
    if attestation.get("trade_id_used_as_candidate_id") is not False:
        return f"lineage_attestation_trade_id_alias:{index}"

    for field in ("candidate_id", "signal_id", "correlation_id"):
        expected = _nonempty_text(approved.get(field))
        if expected is None:
            return f"lineage_identity_missing:{index}:{field}"
        if _nonempty_text(attestation.get(field)) != expected:
            return f"lineage_attestation_identity_mismatch:{index}:{field}"

    envelope = observed.get(DECISION_LEDGER_KEY)
    if not isinstance(envelope, Mapping):
        return f"decision_ledger_envelope_missing:{index}"

    for field in ("candidate_id", "signal_id", "correlation_id"):
        expected = _nonempty_text(approved.get(field))
        actual = _nonempty_text(envelope.get(field))
        if actual != expected:
            return f"decision_ledger_identity_mismatch:{index}:{field}"

    if _nonempty_text(envelope.get("decision_event_id")) is None:
        return f"decision_ledger_event_id_missing:{index}"
    if _nonempty_text(envelope.get("decision_payload_sha256")) is None:
        return f"decision_ledger_payload_sha256_missing:{index}"

    return None


def _mapping_sequence(value: Any) -> tuple[Mapping[str, Any], ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        raise CandidateLineageError("signal_batch_must_be_sequence_not_mapping")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise CandidateLineageError("signal_batch_must_be_sequence")
    output: list[Mapping[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise CandidateLineageError("signal_batch_item_must_be_mapping")
        output.append(item)
    return tuple(output)


def _require_equal_text(actual: Any, expected: str, reason: str) -> None:
    if str(actual or "").strip() != str(expected).strip():
        raise CandidateLineageError(reason)


def _nonempty_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None
