"""Strict read-only projection from one closed Paper trade to lineage.

The module closes the prospective identity contract:

    candidate_id
        -> signal_id
        -> decision_event_id
        -> paper_trade_id

It does not discover or reconstruct a decision. A complete
``StrictDecisionProjectionV1`` must be supplied explicitly, and the closed
Paper trade must carry that exact decision event in ``enter_tag``.

There is deliberately no SQLite reader, Decision Ledger writer, Freqtrade
integration, RiskManager integration, timestamp-nearest matching, symbol/side
candidate inference or historical backfill in this module.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from smartcrypto.execution.decision_ledger_runtime_profile_v1.contracts import (
    RuntimeTradeObservationInputV1,
)
from smartcrypto.execution.decision_ledger_runtime_profile_v1.identifiers import (
    canonical_mapping_sha256,
    normalize_symbol,
)

from .contracts import (
    StrictDecisionProjectionV1,
    StrictTradeLinkProjectionV1,
)
from .mapper import CandidateLineageError, project_strict_trade_link


SCHEMA_VERSION = (
    "paper_candidate_trade_lineage_closed_paper_trade_projection_v1"
)

_DECISION_EVENT_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$"
)

_FORBIDDEN_TRADE_IDENTITY_FIELDS = frozenset(
    {
        "candidate_id",
        "signal_id",
        "correlation_id",
        "decision_event_id",
        "parent_event_id",
    }
)


@dataclass(frozen=True)
class StrictClosedPaperTradeLinkReportV1:
    """Machine-readable status for one read-only trade-link projection."""

    status: str
    reason: str
    decision_event_id: str | None
    paper_trade_id: int | None
    projection_created: bool

    def model_dump(self, *, mode: str = "json") -> dict[str, Any]:
        del mode
        return {
            "schema_version": SCHEMA_VERSION,
            "status": self.status,
            "reason": self.reason,
            "decision_event_id": self.decision_event_id,
            "paper_trade_id": self.paper_trade_id,
            "projection_created": self.projection_created,
            "read_only": True,
            "strict_decision_projection_required": True,
            "explicit_decision_event_id_required": True,
            "writer_invoked": False,
            "writes_runtime": False,
            "writes_sqlite": False,
            "reads_sqlite": False,
            "runtime_integration_executed": False,
            "operational_authority": False,
            "changes_risk": False,
            "changes_model": False,
            "changes_strategy": False,
            "changes_stake": False,
            "changes_leverage": False,
            "sends_orders": False,
            "exchange_private_access": False,
            "historical_backfill": False,
            "timestamp_nearest_matching_allowed": False,
            "symbol_side_candidate_inference_allowed": False,
            "trade_id_used_as_candidate_id": False,
            "post_outcome_identity_override_allowed": False,
        }


@dataclass(frozen=True)
class StrictClosedPaperTradeLinkOutcomeV1:
    """Projection plus fail-closed attribution report."""

    projection: StrictTradeLinkProjectionV1 | None
    report: StrictClosedPaperTradeLinkReportV1


def extract_explicit_decision_event_id(enter_tag: Any) -> str:
    """Extract exactly one explicit decision event from a Paper ``enter_tag``.

    The parser is token-based rather than delimiter-consuming regex matching.
    This matters because adjacent duplicate tokens share the ``|`` delimiter;
    a regex that consumes the trailing delimiter can silently skip the second
    occurrence.

    No other trade field may be used as a substitute correlation key.
    """

    if not isinstance(enter_tag, str) or not enter_tag.strip():
        raise CandidateLineageError(
            "closed_paper_trade_enter_tag_missing"
        )

    values: list[str] = []
    for raw_segment in enter_tag.strip().split("|"):
        segment = raw_segment.strip()
        if not segment.startswith("decision_event_id="):
            continue

        key, separator, value = segment.partition("=")
        if key != "decision_event_id" or separator != "=":
            continue

        normalized = value.strip()
        if not normalized or not _DECISION_EVENT_ID_PATTERN.fullmatch(
            normalized
        ):
            raise CandidateLineageError(
                "closed_paper_trade_decision_event_id_invalid"
            )
        values.append(normalized)

    if not values:
        raise CandidateLineageError(
            "closed_paper_trade_decision_event_id_missing"
        )
    if len(values) != 1:
        raise CandidateLineageError(
            "closed_paper_trade_decision_event_id_ambiguous"
        )
    return values[0]


def project_closed_paper_trade_link_readonly(
    *,
    decision: StrictDecisionProjectionV1 | Mapping[str, Any],
    trade_row: Mapping[str, Any],
    source_database_sha256: str,
    source_table: str = "trades",
) -> StrictClosedPaperTradeLinkOutcomeV1:
    """Project one exact closed Paper trade without persistence.

    Attribution fails closed when the explicit ``decision_event_id`` is absent,
    ambiguous or does not equal the supplied strict decision projection. Such a
    failure has no execution semantics because the trade is already an observed
    post-execution record and this function performs no operational I/O.
    """

    decision_event_id: str | None = None
    paper_trade_id: int | None = None

    try:
        resolved_decision = _resolve_decision(decision)
        target = resolved_decision.decision_projection.target_payload

        if target.final_decision.value != "ALLOW":
            raise CandidateLineageError(
                "closed_paper_trade_requires_allowed_decision"
            )
        if target.risk_decision.value != "APPROVED":
            raise CandidateLineageError(
                "closed_paper_trade_requires_risk_approved_decision"
            )

        if not isinstance(trade_row, Mapping):
            raise CandidateLineageError(
                "closed_paper_trade_row_must_be_mapping"
            )

        forbidden = sorted(
            str(key)
            for key in trade_row
            if str(key) in _FORBIDDEN_TRADE_IDENTITY_FIELDS
        )
        if forbidden:
            raise CandidateLineageError(
                "closed_paper_trade_identity_override_forbidden:"
                + ",".join(forbidden)
            )

        paper_trade_id = _required_positive_trade_id(trade_row)
        _require_closed_trade(trade_row)

        decision_event_id = extract_explicit_decision_event_id(
            trade_row.get("enter_tag")
        )
        if decision_event_id != target.event_id:
            raise CandidateLineageError(
                "closed_paper_trade_decision_event_id_mismatch"
            )

        pair = _required_text(trade_row, "pair")
        side = _side_from_is_short(trade_row.get("is_short"))
        execution_timestamp = _required_utc_timestamp(
            trade_row,
            "open_date",
        )

        source_hash = _required_sha256_text(
            source_database_sha256,
            "source_database_sha256",
        )
        source_table_value = _required_nonempty_text_value(
            source_table,
            "source_table",
        )

        row_fingerprint = canonical_mapping_sha256(
            {
                "id": paper_trade_id,
                "pair": pair,
                "is_short": side == "short",
                "is_open": False,
                "open_date": execution_timestamp,
                "enter_tag": str(trade_row["enter_tag"]).strip(),
            }
        )

        observation = RuntimeTradeObservationInputV1(
            trade_id=paper_trade_id,
            execution_timestamp=execution_timestamp,
            observed_pair=pair,
            observed_symbol=normalize_symbol(pair),
            observed_side=side,
            source_database_sha256=source_hash,
            source_table=source_table_value,
            source_row_fingerprint=row_fingerprint,
            link_reason="explicit_decision_event_id_in_enter_tag",
        )

        projection = project_strict_trade_link(
            resolved_decision,
            observation,
        )
        projected_target = projection.trade_link_projection.target_payload

        if projected_target.trade_id != paper_trade_id:
            raise CandidateLineageError(
                "closed_paper_trade_projected_trade_id_mismatch"
            )
        if projected_target.parent_event_id != decision_event_id:
            raise CandidateLineageError(
                "closed_paper_trade_projected_parent_event_mismatch"
            )

        return StrictClosedPaperTradeLinkOutcomeV1(
            projection=projection,
            report=StrictClosedPaperTradeLinkReportV1(
                status="ok",
                reason="strict_closed_paper_trade_link_projected_readonly",
                decision_event_id=decision_event_id,
                paper_trade_id=paper_trade_id,
                projection_created=True,
            ),
        )
    except CandidateLineageError as exc:
        return _blocked(
            reason=exc.reason,
            decision_event_id=decision_event_id,
            paper_trade_id=paper_trade_id,
        )
    except (ValidationError, TypeError, ValueError) as exc:
        return _blocked(
            reason=(
                "strict_closed_paper_trade_projection_invalid:"
                f"{type(exc).__name__}"
            ),
            decision_event_id=decision_event_id,
            paper_trade_id=paper_trade_id,
        )


def _resolve_decision(
    value: StrictDecisionProjectionV1 | Mapping[str, Any],
) -> StrictDecisionProjectionV1:
    if isinstance(value, StrictDecisionProjectionV1):
        return value
    if not isinstance(value, Mapping):
        raise CandidateLineageError(
            "strict_closed_paper_trade_decision_must_be_projection"
        )
    try:
        return StrictDecisionProjectionV1.model_validate(value)
    except ValidationError as exc:
        raise CandidateLineageError(
            "strict_closed_paper_trade_decision_projection_invalid"
        ) from exc


def _required_positive_trade_id(source: Mapping[str, Any]) -> int:
    value = source.get("id")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CandidateLineageError(
            "closed_paper_trade_id_must_be_positive_integer"
        )
    return value


def _require_closed_trade(source: Mapping[str, Any]) -> None:
    if "is_open" not in source:
        raise CandidateLineageError(
            "closed_paper_trade_is_open_missing"
        )
    value = source.get("is_open")
    if isinstance(value, bool):
        is_open = value
    elif isinstance(value, int) and value in {0, 1}:
        is_open = bool(value)
    else:
        raise CandidateLineageError(
            "closed_paper_trade_is_open_invalid"
        )
    if is_open:
        raise CandidateLineageError(
            "closed_paper_trade_required"
        )


def _side_from_is_short(value: Any) -> str:
    if isinstance(value, bool):
        return "short" if value else "long"
    if isinstance(value, int) and value in {0, 1}:
        return "short" if value == 1 else "long"
    raise CandidateLineageError(
        "closed_paper_trade_is_short_invalid"
    )


def _required_text(source: Mapping[str, Any], field: str) -> str:
    if field not in source:
        raise CandidateLineageError(
            f"closed_paper_trade_required_field_missing:{field}"
        )
    return _required_nonempty_text_value(source.get(field), field)


def _required_nonempty_text_value(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CandidateLineageError(
            f"closed_paper_trade_required_text_invalid:{field}"
        )
    return value.strip()


def _required_sha256_text(value: Any, field: str) -> str:
    text = _required_nonempty_text_value(value, field).lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise CandidateLineageError(
            f"closed_paper_trade_sha256_invalid:{field}"
        )
    return text


def _required_utc_timestamp(
    source: Mapping[str, Any],
    field: str,
) -> datetime:
    if field not in source:
        raise CandidateLineageError(
            f"closed_paper_trade_required_field_missing:{field}"
        )
    value = source.get(field)
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            raise CandidateLineageError(
                f"closed_paper_trade_timestamp_invalid:{field}"
            )
        try:
            parsed = datetime.fromisoformat(
                text[:-1] + "+00:00" if text.endswith("Z") else text
            )
        except ValueError as exc:
            raise CandidateLineageError(
                f"closed_paper_trade_timestamp_invalid:{field}"
            ) from exc

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CandidateLineageError(
            f"closed_paper_trade_timestamp_not_timezone_aware:{field}"
        )
    if parsed.utcoffset().total_seconds() != 0:
        raise CandidateLineageError(
            f"closed_paper_trade_timestamp_not_utc:{field}"
        )
    return parsed.astimezone(timezone.utc)


def _blocked(
    *,
    reason: str,
    decision_event_id: str | None,
    paper_trade_id: int | None,
) -> StrictClosedPaperTradeLinkOutcomeV1:
    return StrictClosedPaperTradeLinkOutcomeV1(
        projection=None,
        report=StrictClosedPaperTradeLinkReportV1(
            status="blocked",
            reason=reason,
            decision_event_id=decision_event_id,
            paper_trade_id=paper_trade_id,
            projection_created=False,
        ),
    )
