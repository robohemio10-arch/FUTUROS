"""Pure strict mappers for prospective candidate-to-trade lineage.

No publisher, writer, SQLite, network client, exchange client, RiskManager or
Freqtrade integration is imported here. The functions validate exact identity
fields and delegate deterministic projection to the existing Decision Ledger
runtime profile.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from smartcrypto.execution.decision_ledger_runtime_profile_v1.contracts import (
    RuntimeTradeObservationInputV1,
)
from smartcrypto.execution.decision_ledger_runtime_profile_v1.identifiers import (
    canonical_mapping_sha256,
)
from smartcrypto.execution.decision_ledger_runtime_profile_v1.mapping import (
    map_runtime_decision,
    map_runtime_trade_link,
)

from .contracts import (
    AuthoritativeCandidateSignalIdentityV1,
    StrictDecisionProjectionV1,
    StrictTradeLinkProjectionV1,
)


STRICT_DECISION_IDENTITY_KEYS = frozenset(
    {
        "candidate_id",
        "signal_id",
        "correlation_id",
    }
)

STRICT_TRADE_OBSERVATION_IDENTITY_KEYS = frozenset(
    {
        "candidate_id",
        "signal_id",
        "correlation_id",
        "decision_event_id",
        "parent_event_id",
    }
)


class CandidateLineageError(ValueError):
    """Controlled fail-closed error carrying one stable machine reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def build_authoritative_signal_identity(
    source_signal: Mapping[str, Any],
) -> AuthoritativeCandidateSignalIdentityV1:
    """Validate exact identity fields from one authoritative signal candidate.

    The adapter accepts only the exact ``candidate_id``, ``signal_id`` and
    ``correlation_id`` fields required by the existing Decision Ledger
    field-source registry. It never falls back to research aliases such as
    ``source_candidate_id`` or ``signal_candidate_id`` and never derives
    identity from trade IDs, timestamps, pair, symbol or side.
    """

    if not isinstance(source_signal, Mapping):
        raise CandidateLineageError("source_signal_must_be_mapping")

    candidate_id = _required_identifier(
        source_signal,
        "candidate_id",
        missing_reason="authoritative_candidate_id_missing",
    )
    signal_id = _required_identifier(
        source_signal,
        "signal_id",
        missing_reason="authoritative_signal_id_missing",
    )
    correlation_id = _required_identifier(
        source_signal,
        "correlation_id",
        missing_reason="authoritative_correlation_id_missing",
    )

    try:
        source_signal_sha256 = canonical_mapping_sha256(dict(source_signal))
    except (TypeError, ValueError) as exc:
        raise CandidateLineageError("source_signal_not_canonically_hashable") from exc

    try:
        return AuthoritativeCandidateSignalIdentityV1(
            candidate_id=candidate_id,
            signal_id=signal_id,
            correlation_id=correlation_id,
            source_signal_sha256=source_signal_sha256,
        )
    except ValidationError as exc:
        raise CandidateLineageError("authoritative_identity_contract_invalid") from exc


def project_strict_decision(
    identity: AuthoritativeCandidateSignalIdentityV1 | Mapping[str, Any],
    decision_fields: Mapping[str, Any],
) -> StrictDecisionProjectionV1:
    """Project one decision while prohibiting identity override or synthesis."""

    resolved_identity = _resolve_identity(identity)
    if not isinstance(decision_fields, Mapping):
        raise CandidateLineageError("decision_fields_must_be_mapping")

    supplied = set(map(str, decision_fields))
    identity_overrides = sorted(supplied & STRICT_DECISION_IDENTITY_KEYS)
    if identity_overrides:
        raise CandidateLineageError(
            "decision_identity_override_forbidden:" + ",".join(identity_overrides)
        )
    if "trade_id" in supplied:
        raise CandidateLineageError("trade_id_forbidden_in_decision_lineage")

    payload = dict(decision_fields)
    payload.update(
        {
            "candidate_id": resolved_identity.candidate_id,
            "signal_id": resolved_identity.signal_id,
            "correlation_id": resolved_identity.correlation_id,
        }
    )

    try:
        projection = map_runtime_decision(payload)
        return StrictDecisionProjectionV1(
            identity=resolved_identity,
            decision_projection=projection,
        )
    except (ValidationError, ValueError) as exc:
        raise CandidateLineageError("strict_decision_projection_failed") from exc


def project_strict_trade_link(
    decision: StrictDecisionProjectionV1 | Mapping[str, Any],
    trade_observation: RuntimeTradeObservationInputV1 | Mapping[str, Any],
) -> StrictTradeLinkProjectionV1:
    """Project a trade link from an already strict decision.

    Candidate/signal/correlation identity is inherited exclusively from the
    sealed decision. The post-execution observation may contribute only the
    authoritative Paper trade observation fields accepted by
    ``RuntimeTradeObservationInputV1``.
    """

    resolved_decision = _resolve_decision(decision)

    if isinstance(trade_observation, Mapping):
        supplied = set(map(str, trade_observation))
        forbidden = sorted(supplied & STRICT_TRADE_OBSERVATION_IDENTITY_KEYS)
        if forbidden:
            raise CandidateLineageError(
                "trade_observation_identity_override_forbidden:"
                + ",".join(forbidden)
            )

    try:
        projection = map_runtime_trade_link(
            resolved_decision.decision_projection,
            trade_observation,
        )
        return StrictTradeLinkProjectionV1(
            identity=resolved_decision.identity,
            decision_event_id=(
                resolved_decision.decision_projection.target_payload.event_id
            ),
            trade_link_projection=projection,
        )
    except (ValidationError, ValueError) as exc:
        raise CandidateLineageError("strict_trade_link_projection_failed") from exc


def _required_identifier(
    source: Mapping[str, Any],
    field: str,
    *,
    missing_reason: str,
) -> str:
    value = source.get(field)
    if value is None:
        raise CandidateLineageError(missing_reason)
    text = str(value).strip()
    if not text:
        raise CandidateLineageError(missing_reason)
    return text


def _resolve_identity(
    value: AuthoritativeCandidateSignalIdentityV1 | Mapping[str, Any],
) -> AuthoritativeCandidateSignalIdentityV1:
    if isinstance(value, AuthoritativeCandidateSignalIdentityV1):
        return value
    try:
        return AuthoritativeCandidateSignalIdentityV1.model_validate(value)
    except ValidationError as exc:
        raise CandidateLineageError("authoritative_identity_contract_invalid") from exc


def _resolve_decision(
    value: StrictDecisionProjectionV1 | Mapping[str, Any],
) -> StrictDecisionProjectionV1:
    if isinstance(value, StrictDecisionProjectionV1):
        return value
    try:
        return StrictDecisionProjectionV1.model_validate(value)
    except ValidationError as exc:
        raise CandidateLineageError("strict_decision_contract_invalid") from exc
