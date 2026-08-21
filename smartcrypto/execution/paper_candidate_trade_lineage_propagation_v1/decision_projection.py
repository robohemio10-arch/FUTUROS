"""Strict in-memory Paper decision projection for prospective lineage.

This module creates a deterministic ``decision_event_id`` only after
RiskManager has approved a signal that already carries the Stage-3C
authoritative lineage attestation.

The projection is evidence-only:
- no Decision Ledger writer is imported or invoked;
- no runtime or SQLite state is written;
- no RiskManager decision is changed;
- failure blocks attribution only and returns the approved baseline unchanged.

Decision context must be explicit. Candidate provenance is resolved by exact
identity against the original selected prediction row. Feature/model context is
never synthesized from wall clock time, symbol/side heuristics or trade data.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .mapper import (
    CandidateLineageError,
    build_authoritative_signal_identity,
    project_strict_decision,
)
from .publication import (
    ATTESTATION_KEY,
    ATTESTATION_SCHEMA,
    DECISION_LEDGER_KEY,
)


SCHEMA_VERSION = "paper_candidate_trade_lineage_strict_decision_in_memory_v1"


@dataclass(frozen=True)
class StrictDecisionInMemoryReportV1:
    status: str
    reason: str
    approved_signal_count: int
    projected_decision_count: int
    blocked_decision_count: int
    publication_blocked: bool
    failures: tuple[dict[str, Any], ...]

    def model_dump(self, *, mode: str = "json") -> dict[str, Any]:
        del mode
        return {
            "schema_version": SCHEMA_VERSION,
            "status": self.status,
            "reason": self.reason,
            "approved_signal_count": self.approved_signal_count,
            "projected_decision_count": self.projected_decision_count,
            "blocked_decision_count": self.blocked_decision_count,
            "publication_blocked": self.publication_blocked,
            "failures": [dict(item) for item in self.failures],
            "writer_invoked": False,
            "writes_runtime": False,
            "writes_sqlite": False,
            "runtime_integration_executed": False,
            "operational_authority": False,
            "changes_risk": False,
            "changes_model": False,
            "sends_orders": False,
            "exchange_private_access": False,
            "historical_backfill": False,
            "timestamp_nearest_matching_allowed": False,
            "symbol_side_candidate_inference_allowed": False,
            "trade_id_used_for_decision_identity": False,
            "partial_batch_publication_allowed": False,
        }


@dataclass(frozen=True)
class StrictDecisionInMemoryOutcomeV1:
    active_signals: tuple[dict[str, Any], ...]
    report: StrictDecisionInMemoryReportV1


def project_strict_decision_envelopes_in_memory(
    *,
    approved_signals: Sequence[Mapping[str, Any]],
    source_rows: Sequence[Mapping[str, Any]],
    decision_timestamp_utc: datetime,
    producer_id: str,
) -> StrictDecisionInMemoryOutcomeV1:
    """Project strict decision envelopes without persistence.

    The batch is intentionally all-or-nothing for attribution. If one approved
    signal cannot be projected exactly, no decision envelope is published for
    the batch; all approved signals are returned unchanged.
    """

    baseline = tuple(dict(item) for item in approved_signals)
    if not baseline:
        return StrictDecisionInMemoryOutcomeV1(
            active_signals=(),
            report=StrictDecisionInMemoryReportV1(
                status="empty",
                reason="no_risk_approved_signals",
                approved_signal_count=0,
                projected_decision_count=0,
                blocked_decision_count=0,
                publication_blocked=False,
                failures=(),
            ),
        )

    decision_timestamp = _require_utc_datetime(
        decision_timestamp_utc,
        "decision_timestamp_utc",
    )
    contexts, context_error = _index_source_contexts(source_rows)
    if context_error is not None:
        return _blocked_batch(baseline, context_error)

    projected: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for index, approved in enumerate(baseline):
        try:
            projected.append(
                _project_one(
                    approved=approved,
                    contexts=contexts,
                    decision_timestamp=decision_timestamp,
                    producer_id=producer_id,
                )
            )
        except CandidateLineageError as exc:
            failures.append(
                {
                    "signal_index": index,
                    "symbol": approved.get("symbol"),
                    "side": approved.get("side"),
                    "reason": str(exc),
                }
            )
        except Exception as exc:
            failures.append(
                {
                    "signal_index": index,
                    "symbol": approved.get("symbol"),
                    "side": approved.get("side"),
                    "reason": (
                        "unexpected_strict_decision_projection_error:"
                        f"{type(exc).__name__}"
                    ),
                }
            )

    if failures:
        return StrictDecisionInMemoryOutcomeV1(
            active_signals=baseline,
            report=StrictDecisionInMemoryReportV1(
                status="blocked",
                reason="strict_decision_projection_incomplete",
                approved_signal_count=len(baseline),
                projected_decision_count=len(projected),
                blocked_decision_count=len(failures),
                publication_blocked=True,
                failures=tuple(failures),
            ),
        )

    return StrictDecisionInMemoryOutcomeV1(
        active_signals=tuple(projected),
        report=StrictDecisionInMemoryReportV1(
            status="ok",
            reason="strict_decision_envelopes_projected_in_memory",
            approved_signal_count=len(baseline),
            projected_decision_count=len(projected),
            blocked_decision_count=0,
            publication_blocked=False,
            failures=(),
        ),
    )


def _project_one(
    *,
    approved: Mapping[str, Any],
    contexts: Mapping[tuple[str, str, str], Mapping[str, Any]],
    decision_timestamp: datetime,
    producer_id: str,
) -> dict[str, Any]:
    if approved.get("risk_approved") is not True:
        raise CandidateLineageError("approved_signal_missing_risk_approval")

    attestation = approved.get(ATTESTATION_KEY)
    if not isinstance(attestation, Mapping):
        raise CandidateLineageError("strict_decision_lineage_attestation_missing")
    if attestation.get("schema_version") != ATTESTATION_SCHEMA:
        raise CandidateLineageError("strict_decision_lineage_attestation_invalid")
    if attestation.get("prospective_only") is not True:
        raise CandidateLineageError("strict_decision_lineage_not_prospective")
    if attestation.get("authoritative_identity") is not True:
        raise CandidateLineageError("strict_decision_lineage_not_authoritative")
    if attestation.get("synthetic_identity") is not False:
        raise CandidateLineageError("strict_decision_lineage_is_synthetic")

    candidate_id = _required_text(approved, "candidate_id")
    signal_id = _required_text(approved, "signal_id")
    correlation_id = _required_text(approved, "correlation_id")

    for field, expected in (
        ("candidate_id", candidate_id),
        ("signal_id", signal_id),
        ("correlation_id", correlation_id),
    ):
        if _required_text(attestation, field) != expected:
            raise CandidateLineageError(
                f"strict_decision_attestation_identity_mismatch:{field}"
            )

    attestation_producer = _required_text(attestation, "producer_id")
    if attestation_producer != producer_id:
        raise CandidateLineageError("strict_decision_producer_id_mismatch")

    research_signal_candidate_id = _required_text(
        attestation,
        "research_signal_candidate_id",
    )
    signal_instance_id = _required_text(attestation, "signal_instance_id")

    context_key = (
        candidate_id,
        research_signal_candidate_id,
        signal_instance_id,
    )
    source = contexts.get(context_key)
    if source is None:
        raise CandidateLineageError("strict_decision_source_context_not_found")

    _require_equal(
        source.get("source_candidate_id"),
        candidate_id,
        "strict_decision_source_candidate_mismatch",
    )
    _require_equal(
        source.get("signal_candidate_id"),
        research_signal_candidate_id,
        "strict_decision_research_signal_candidate_mismatch",
    )
    _require_equal(
        source.get("signal_instance_id"),
        signal_instance_id,
        "strict_decision_signal_instance_mismatch",
    )
    _require_equal(
        source.get("pair"),
        approved.get("pair"),
        "strict_decision_pair_context_mismatch",
    )
    _require_equal(
        source.get("symbol"),
        approved.get("symbol"),
        "strict_decision_symbol_context_mismatch",
    )
    _require_equal(
        str(source.get("side") or "").lower(),
        str(approved.get("side") or "").lower(),
        "strict_decision_side_context_mismatch",
    )

    identity = build_authoritative_signal_identity(approved)
    risk_checked_at = _required_utc_from_mapping(
        approved,
        "risk_checked_at_utc",
    )
    if decision_timestamp < risk_checked_at:
        raise CandidateLineageError(
            "strict_decision_timestamp_before_risk_check"
        )

    feature_timestamp = _required_explicit_utc_alias(
        source,
        ("feature_timestamp_utc", "feature_timestamp"),
        "feature_timestamp",
    )

    decision_fields = {
        "runtime_mode": "paper",
        "pair": _required_text(approved, "pair"),
        "symbol": _required_text(approved, "symbol"),
        "side": _required_text(approved, "side").lower(),
        "feature_timestamp": feature_timestamp,
        "decision_timestamp": decision_timestamp,
        "risk_checked_at_utc": risk_checked_at,
        "feature_contract_version": _required_text(
            source,
            "feature_contract_version",
        ),
        "feature_hash": _required_sha256(source, "feature_hash"),
        "model_id": _required_text(source, "model_id"),
        "model_version": _required_text(approved, "model_version"),
        "model_hash": _required_sha256(source, "model_hash"),
        "qlib_score": _required_finite(approved, "score"),
        "calibrated_probability": _optional_probability(
            source.get("calibrated_probability")
        ),
        "expected_net_pnl": _optional_finite(source.get("expected_net_pnl")),
        "fast_stop_probability": _optional_probability(
            source.get("fast_stop_probability")
        ),
        "regime": _required_regime(source),
        "alignment": _required_text(source, "alignment").lower(),
        "ai_shadow_decision": _required_text(
            source,
            "ai_shadow_decision",
        ).upper(),
        "ai_shadow_reasons": _text_tuple(
            source.get("ai_shadow_reasons"),
            field="ai_shadow_reasons",
        ),
        "risk_approved": True,
        "risk_reasons": _text_tuple(
            approved.get("risk_reasons"),
            field="risk_reasons",
        ),
        "risk_policy_id": _required_text(approved, "risk_policy_id"),
        "risk_config_hash": _required_sha256(
            approved,
            "risk_config_hash",
        ),
        "approved_stake_usdt": _approved_positive_value(
            approved,
            primary="approved_stake_usdt",
            fallback="max_position_usdt",
        ),
        "approved_leverage": _approved_positive_value(
            approved,
            primary="approved_leverage",
            fallback="leverage",
        ),
        "final_decision": "ALLOW",
        "final_reasons": _final_reasons(approved),
        "source_signal_sha256": identity.source_signal_sha256,
    }

    strict = project_strict_decision(identity, decision_fields)
    target = strict.decision_projection.target_payload

    envelope = {
        "schema_version": SCHEMA_VERSION,
        "decision_event_id": target.event_id,
        "decision_payload_sha256": target.payload_sha256,
        "candidate_id": target.candidate_id,
        "signal_id": target.signal_id,
        "correlation_id": target.correlation_id,
        "decision_timestamp": target.decision_timestamp.isoformat(),
        "projection_type": "strict_in_memory",
        "writer_invoked": False,
        "writes_runtime": False,
        "operational_authority": False,
    }

    output = dict(approved)
    if DECISION_LEDGER_KEY in output:
        raise CandidateLineageError(
            "strict_decision_existing_envelope_override_forbidden"
        )
    output[DECISION_LEDGER_KEY] = envelope
    return output


def _index_source_contexts(
    source_rows: Sequence[Mapping[str, Any]],
) -> tuple[
    dict[tuple[str, str, str], Mapping[str, Any]],
    str | None,
]:
    output: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for row in source_rows:
        if not isinstance(row, Mapping):
            continue
        candidate_id = _optional_text(row.get("source_candidate_id"))
        signal_candidate_id = _optional_text(row.get("signal_candidate_id"))
        signal_instance_id = _optional_text(row.get("signal_instance_id"))
        if (
            candidate_id is None
            or signal_candidate_id is None
            or signal_instance_id is None
        ):
            continue
        key = (candidate_id, signal_candidate_id, signal_instance_id)
        if key in output:
            return {}, "duplicate_strict_decision_source_context"
        output[key] = row
    return output, None


def _blocked_batch(
    baseline: Sequence[Mapping[str, Any]],
    reason: str,
) -> StrictDecisionInMemoryOutcomeV1:
    copied = tuple(dict(item) for item in baseline)
    failures = tuple(
        {
            "signal_index": index,
            "symbol": item.get("symbol"),
            "side": item.get("side"),
            "reason": reason,
        }
        for index, item in enumerate(copied)
    )
    return StrictDecisionInMemoryOutcomeV1(
        active_signals=copied,
        report=StrictDecisionInMemoryReportV1(
            status="blocked",
            reason=reason,
            approved_signal_count=len(copied),
            projected_decision_count=0,
            blocked_decision_count=len(copied),
            publication_blocked=True,
            failures=failures,
        ),
    )


def _required_text(source: Mapping[str, Any], field: str) -> str:
    value = _optional_text(source.get(field))
    if value is None:
        raise CandidateLineageError(
            f"strict_decision_required_field_missing:{field}"
        )
    return value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _require_equal(actual: Any, expected: Any, reason: str) -> None:
    if str(actual or "").strip() != str(expected or "").strip():
        raise CandidateLineageError(reason)


def _required_sha256(source: Mapping[str, Any], field: str) -> str:
    value = _required_text(source, field).lower()
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise CandidateLineageError(
            f"strict_decision_invalid_sha256:{field}"
        )
    return value


def _required_utc_from_mapping(
    source: Mapping[str, Any],
    field: str,
) -> datetime:
    value = source.get(field)
    if value is None:
        raise CandidateLineageError(
            f"strict_decision_required_field_missing:{field}"
        )
    return _coerce_utc_datetime(value, field)


def _required_explicit_utc_alias(
    source: Mapping[str, Any],
    fields: tuple[str, ...],
    semantic_name: str,
) -> datetime:
    present = [
        (field, source.get(field))
        for field in fields
        if source.get(field) is not None
        and str(source.get(field)).strip()
    ]
    if not present:
        raise CandidateLineageError(
            f"strict_decision_required_field_missing:{semantic_name}"
        )
    parsed = [
        (field, _coerce_utc_datetime(value, field))
        for field, value in present
    ]
    first = parsed[0][1]
    if any(value != first for _, value in parsed[1:]):
        raise CandidateLineageError(
            f"strict_decision_ambiguous_timestamp:{semantic_name}"
        )
    return first


def _coerce_utc_datetime(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        return _require_utc_datetime(value, field)
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CandidateLineageError(
            f"strict_decision_invalid_timestamp:{field}"
        ) from exc
    return _require_utc_datetime(parsed, field)


def _require_utc_datetime(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CandidateLineageError(
            f"strict_decision_timestamp_not_timezone_aware:{field}"
        )
    if value.utcoffset().total_seconds() != 0:
        raise CandidateLineageError(
            f"strict_decision_timestamp_not_utc:{field}"
        )
    return value.astimezone(timezone.utc)


def _required_finite(source: Mapping[str, Any], field: str) -> float:
    value = source.get(field)
    if value is None:
        raise CandidateLineageError(
            f"strict_decision_required_field_missing:{field}"
        )
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise CandidateLineageError(
            f"strict_decision_invalid_number:{field}"
        ) from exc
    if not math.isfinite(number):
        raise CandidateLineageError(
            f"strict_decision_non_finite_number:{field}"
        )
    return number


def _optional_finite(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise CandidateLineageError(
            "strict_decision_invalid_optional_number"
        ) from exc
    if not math.isfinite(number):
        raise CandidateLineageError(
            "strict_decision_non_finite_optional_number"
        )
    return number


def _optional_probability(value: Any) -> float | None:
    number = _optional_finite(value)
    if number is None:
        return None
    if not 0.0 <= number <= 1.0:
        raise CandidateLineageError(
            "strict_decision_probability_out_of_range"
        )
    return number


def _required_regime(source: Mapping[str, Any]) -> str:
    regime = _optional_text(source.get("regime"))
    if regime is None:
        regime = _optional_text(source.get("market_regime"))
    if regime is None:
        raise CandidateLineageError(
            "strict_decision_required_field_missing:regime"
        )
    return regime


def _text_tuple(value: Any, *, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if not isinstance(value, Sequence) or isinstance(
        value,
        (bytes, bytearray),
    ):
        raise CandidateLineageError(
            f"strict_decision_invalid_text_sequence:{field}"
        )
    output = tuple(
        str(item).strip()
        for item in value
        if str(item).strip()
    )
    return output


def _approved_positive_value(
    source: Mapping[str, Any],
    *,
    primary: str,
    fallback: str,
) -> float:
    raw = source.get(primary)
    if raw is None:
        raw = source.get(fallback)
    if raw is None:
        raise CandidateLineageError(
            f"strict_decision_required_field_missing:{primary}"
        )
    try:
        number = float(raw)
    except (TypeError, ValueError) as exc:
        raise CandidateLineageError(
            f"strict_decision_invalid_number:{primary}"
        ) from exc
    if not math.isfinite(number) or number <= 0.0:
        raise CandidateLineageError(
            f"strict_decision_non_positive_value:{primary}"
        )
    return number


def _final_reasons(source: Mapping[str, Any]) -> tuple[str, ...]:
    provided = _text_tuple(
        source.get("final_reasons"),
        field="final_reasons",
    )
    return provided or ("risk_manager_approved",)
