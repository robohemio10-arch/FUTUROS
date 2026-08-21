"""Strict research-candidate to concrete-signal identity materialization.

This module is pure and isolated. It does not import publishers, runtime
writers, Freqtrade, RiskManager, SQLite, Redis, exchange clients, or network
clients. Its only job is to prove the research candidate's registry identity
and materialize deterministic per-signal IDs from an explicit signal occurrence
key supplied by the signal production boundary.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from smartcrypto.execution.decision_ledger_runtime_profile_v1.identifiers import (
    canonical_mapping_sha256,
    normalize_symbol,
)

from .contracts import (
    ConcreteSignalIdentityBindingV1,
    ConcreteSignalOccurrenceV1,
    ResearchSignalCandidateReferenceV1,
)
from .mapper import CandidateLineageError, build_authoritative_signal_identity


LEGACY_REGISTRY_FALLBACK_PATTERN = re.compile(r"^registry-candidate-\d+$")
FORBIDDEN_POST_OUTCOME_FIELD_PATTERNS: tuple[str, ...] = (
    "label",
    "target",
    "outcome",
    "pnl",
    "profit",
    "win_loss",
    "future_return",
    "future_ret",
)


def build_research_candidate_reference(
    research_candidate: Mapping[str, Any],
    registry_candidate: Mapping[str, Any],
    *,
    producer_id: str,
) -> ResearchSignalCandidateReferenceV1:
    """Prove that a research candidate points to a real registry candidate.

    The current research producer exposes ``source_candidate_id`` and
    ``signal_candidate_id``. ``source_candidate_id`` may only cross into runtime
    candidate identity after exact equality with the registry candidate's
    authoritative ``candidate_id`` is proven. The legacy producer fallback
    ``registry-candidate-{index}`` is explicitly rejected.
    """

    if not isinstance(research_candidate, Mapping):
        raise CandidateLineageError("research_candidate_must_be_mapping")
    if not isinstance(registry_candidate, Mapping):
        raise CandidateLineageError("registry_candidate_must_be_mapping")

    forbidden_path = _first_forbidden_post_outcome_path(research_candidate)
    if forbidden_path is not None:
        raise CandidateLineageError(
            "research_candidate_contains_post_outcome_field:" + forbidden_path
        )

    source_candidate_id = _required_text(
        research_candidate,
        "source_candidate_id",
        "research_source_candidate_id_missing",
    )
    signal_candidate_id = _required_text(
        research_candidate,
        "signal_candidate_id",
        "research_signal_candidate_id_missing",
    )
    registry_candidate_id = _required_text(
        registry_candidate,
        "candidate_id",
        "registry_candidate_id_missing",
    )
    actionability = _required_text(
        research_candidate,
        "signal_actionability",
        "research_signal_actionability_missing",
    )

    if LEGACY_REGISTRY_FALLBACK_PATTERN.fullmatch(source_candidate_id):
        raise CandidateLineageError("legacy_registry_candidate_fallback_not_authoritative")
    if LEGACY_REGISTRY_FALLBACK_PATTERN.fullmatch(registry_candidate_id):
        raise CandidateLineageError("legacy_registry_candidate_fallback_not_authoritative")
    if source_candidate_id != registry_candidate_id:
        raise CandidateLineageError("research_candidate_registry_identity_mismatch")
    if actionability not in {"research_observation_only", "blocked"}:
        raise CandidateLineageError("research_signal_actionability_invalid")

    expected_signal_candidate_id = _expected_signal_candidate_id(research_candidate)
    if signal_candidate_id != expected_signal_candidate_id:
        raise CandidateLineageError("research_signal_candidate_id_integrity_mismatch")

    try:
        return ResearchSignalCandidateReferenceV1(
            producer_id=producer_id,
            source_candidate_id=source_candidate_id,
            signal_candidate_id=signal_candidate_id,
            registry_candidate_id=registry_candidate_id,
            signal_actionability=actionability,
            research_candidate_sha256=canonical_mapping_sha256(
                dict(research_candidate)
            ),
            registry_candidate_sha256=canonical_mapping_sha256(
                dict(registry_candidate)
            ),
        )
    except (ValidationError, TypeError, ValueError) as exc:
        raise CandidateLineageError("research_candidate_reference_contract_invalid") from exc


def materialize_concrete_signal_identity(
    research_candidate: Mapping[str, Any],
    registry_candidate: Mapping[str, Any],
    occurrence: ConcreteSignalOccurrenceV1 | Mapping[str, Any],
    *,
    producer_id: str,
) -> ConcreteSignalIdentityBindingV1:
    """Materialize one concrete per-signal identity without runtime side effects.

    No identity is created from a static research row alone. An explicit
    ``signal_instance_id`` and occurrence fingerprint are required. The runtime
    candidate_id is the verified registry candidate ID propagated unchanged.
    signal_id and correlation_id are deterministic producer identities over
    pre-execution occurrence fields only.
    """

    reference = build_research_candidate_reference(
        research_candidate,
        registry_candidate,
        producer_id=producer_id,
    )
    if reference.signal_actionability != "research_observation_only":
        raise CandidateLineageError(
            "research_candidate_not_materialization_eligible:"
            + reference.signal_actionability
        )

    resolved_occurrence = _resolve_occurrence(occurrence)
    if resolved_occurrence.producer_id != producer_id:
        raise CandidateLineageError("materialization_producer_id_mismatch")

    _validate_occurrence_scope(research_candidate, resolved_occurrence)

    identity_basis = {
        "schema": "paper_candidate_trade_lineage_concrete_signal_identity_v1",
        "producer_id": producer_id,
        "registry_candidate_id": reference.registry_candidate_id,
        "research_signal_candidate_id": reference.signal_candidate_id,
        "signal_instance_id": resolved_occurrence.signal_instance_id,
        "signal_timestamp_utc": resolved_occurrence.signal_timestamp_utc,
        "pair": resolved_occurrence.pair,
        "symbol": resolved_occurrence.symbol,
        "side": resolved_occurrence.side,
        "regime": resolved_occurrence.regime,
        "occurrence_source_sha256": resolved_occurrence.occurrence_source_sha256,
    }

    signal_digest = canonical_mapping_sha256(
        {**identity_basis, "identity_kind": "signal_id"}
    )
    correlation_digest = canonical_mapping_sha256(
        {**identity_basis, "identity_kind": "correlation_id"}
    )
    signal_id = f"signal:{signal_digest[:40]}"
    correlation_id = f"correlation:{correlation_digest[:40]}"

    concrete_signal = {
        "candidate_id": reference.registry_candidate_id,
        "signal_id": signal_id,
        "correlation_id": correlation_id,
        "producer_id": producer_id,
        "research_signal_candidate_id": reference.signal_candidate_id,
        "signal_instance_id": resolved_occurrence.signal_instance_id,
        "signal_timestamp_utc": resolved_occurrence.signal_timestamp_utc,
        "pair": resolved_occurrence.pair,
        "symbol": resolved_occurrence.symbol,
        "side": resolved_occurrence.side,
        "regime": resolved_occurrence.regime,
        "occurrence_source_sha256": resolved_occurrence.occurrence_source_sha256,
    }

    identity = build_authoritative_signal_identity(concrete_signal)
    materialization_sha256 = canonical_mapping_sha256(
        {
            "research_reference": reference.model_dump(mode="python"),
            "occurrence": resolved_occurrence.model_dump(mode="python"),
            "candidate_id": identity.candidate_id,
            "signal_id": identity.signal_id,
            "correlation_id": identity.correlation_id,
        }
    )

    try:
        return ConcreteSignalIdentityBindingV1(
            research_reference=reference,
            occurrence=resolved_occurrence,
            identity=identity,
            materialization_sha256=materialization_sha256,
        )
    except ValidationError as exc:
        raise CandidateLineageError("concrete_signal_identity_binding_invalid") from exc


def _resolve_occurrence(
    value: ConcreteSignalOccurrenceV1 | Mapping[str, Any],
) -> ConcreteSignalOccurrenceV1:
    if isinstance(value, ConcreteSignalOccurrenceV1):
        return value
    try:
        return ConcreteSignalOccurrenceV1.model_validate(value)
    except ValidationError as exc:
        raise CandidateLineageError("concrete_signal_occurrence_contract_invalid") from exc


def _validate_occurrence_scope(
    research_candidate: Mapping[str, Any],
    occurrence: ConcreteSignalOccurrenceV1,
) -> None:
    try:
        expected_symbol = normalize_symbol(occurrence.pair)
    except ValueError as exc:
        raise CandidateLineageError("concrete_signal_pair_invalid") from exc
    if expected_symbol != occurrence.symbol:
        raise CandidateLineageError("concrete_signal_symbol_pair_mismatch")

    symbol_scope = _string_sequence(research_candidate.get("symbol_scope"))
    side_scope = tuple(item.lower() for item in _string_sequence(research_candidate.get("side_scope")))
    regime_scope = _string_sequence(research_candidate.get("regime_scope"))

    if not symbol_scope:
        raise CandidateLineageError("research_candidate_symbol_scope_missing")
    if occurrence.symbol not in symbol_scope:
        raise CandidateLineageError("concrete_signal_symbol_outside_research_scope")

    if not side_scope:
        raise CandidateLineageError("research_candidate_side_scope_missing")
    if occurrence.side.lower() not in side_scope:
        raise CandidateLineageError("concrete_signal_side_outside_research_scope")

    if regime_scope and occurrence.regime not in regime_scope:
        raise CandidateLineageError("concrete_signal_regime_outside_research_scope")

    direction = str(research_candidate.get("signal_direction") or "unknown").lower()
    if direction in {"long", "short"} and direction != occurrence.side.lower():
        raise CandidateLineageError("concrete_signal_direction_mismatch")


def _expected_signal_candidate_id(research_candidate: Mapping[str, Any]) -> str:
    parts = (
        research_candidate.get("source_candidate_id"),
        research_candidate.get("source_model_candidate_type"),
        research_candidate.get("source_id"),
        research_candidate.get("threshold"),
        research_candidate.get("signal_actionability"),
    )
    digest = _sha256_text("|".join(str(part) for part in parts))
    return f"signal_candidate_{digest[:16]}"


def _sha256_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _required_text(
    source: Mapping[str, Any],
    field: str,
    reason: str,
) -> str:
    value = source.get(field)
    if value is None:
        raise CandidateLineageError(reason)
    text = str(value).strip()
    if not text:
        raise CandidateLineageError(reason)
    return text


def _string_sequence(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _first_forbidden_post_outcome_path(
    value: Any,
    *,
    prefix: str = "",
) -> str | None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key)
            path = f"{prefix}.{key_text}" if prefix else key_text
            normalized = key_text.strip().lower()
            if any(pattern in normalized for pattern in FORBIDDEN_POST_OUTCOME_FIELD_PATTERNS):
                return path
            found = _first_forbidden_post_outcome_path(nested, prefix=path)
            if found is not None:
                return found
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, nested in enumerate(value):
            path = f"{prefix}[{index}]"
            found = _first_forbidden_post_outcome_path(nested, prefix=path)
            if found is not None:
                return found
    return None
