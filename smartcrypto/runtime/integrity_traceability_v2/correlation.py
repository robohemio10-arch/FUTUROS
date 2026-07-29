"""Materialized paper/shadow correlation evidence without operational authority."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal, Mapping, Sequence

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from .atomic_writer import AtomicWritePolicy, AtomicWriteResult, atomic_write_json

SCHEMA_VERSION: Literal["runtime_integrity_traceability_v2"] = (
    "runtime_integrity_traceability_v2"
)
SOURCE_TYPES = (
    "market_event",
    "prediction",
    "shadow_decision",
    "risk_decision",
    "signal",
    "freqtrade_trade",
    "feedback",
    "training_sample",
)
TIMESTAMP_ONLY_METHODS = frozenset(
    {
        "timestamp",
        "timestamp_only",
        "nearest_timestamp",
        "nearest_time",
        "time_window",
    }
)
REQUIRED_IDENTIFIERS = (
    "market_event_id",
    "prediction_id",
    "model_version",
    "shadow_decision_id",
    "risk_decision_id",
    "signal_id",
    "freqtrade_trade_id",
    "order_ids",
    "feedback_event_id",
    "training_sample_id",
)
UNIQUE_IDENTIFIERS = tuple(
    name for name in REQUIRED_IDENTIFIERS if name != "model_version"
)

Identifier = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
]


class FrozenContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class TraceabilitySafetyFlagsV2(FrozenContract):
    paper_only: Literal[True] = True
    shadow_only: Literal[True] = True
    research_only: Literal[True] = True
    operational_authority: Literal[False] = False
    live_trading_enabled: Literal[False] = False
    canary_release_allowed: Literal[False] = False
    order_submission_enabled: Literal[False] = False
    real_order_submission_enabled: Literal[False] = False
    exchange_private_access: Literal[False] = False
    sends_orders: Literal[False] = False
    changes_risk: Literal[False] = False
    automatic_promotion_allowed: Literal[False] = False
    publishes_active_signals: Literal[False] = False
    writes_financial_ledger: Literal[False] = False


class CorrelationSourceEventV2(FrozenContract):
    source_type: Literal[
        "market_event",
        "prediction",
        "shadow_decision",
        "risk_decision",
        "signal",
        "freqtrade_trade",
        "feedback",
        "training_sample",
    ]
    correlation_id: Identifier | None = None
    match_method: Identifier = "explicit_correlation_id"
    source_reference: Identifier
    market_event_id: Identifier | None = None
    prediction_id: Identifier | None = None
    model_version: Identifier | None = None
    shadow_decision_id: Identifier | None = None
    risk_decision_id: Identifier | None = None
    signal_id: Identifier | None = None
    freqtrade_trade_id: Identifier | None = None
    order_ids: tuple[Identifier, ...] = ()
    feedback_event_id: Identifier | None = None
    training_sample_id: Identifier | None = None
    decision_event_id: Identifier | None = None

    @field_validator("order_ids")
    @classmethod
    def _unique_order_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate_order_ids_in_source_event")
        return value


class CorrelationRecordV2(FrozenContract):
    schema_version: Literal["runtime_integrity_traceability_record_v2"] = (
        "runtime_integrity_traceability_record_v2"
    )
    correlation_id: Identifier
    market_event_id: Identifier
    prediction_id: Identifier
    model_version: Identifier
    shadow_decision_id: Identifier
    risk_decision_id: Identifier
    signal_id: Identifier
    freqtrade_trade_id: Identifier
    order_ids: tuple[Identifier, ...] = Field(min_length=1)
    feedback_event_id: Identifier
    training_sample_id: Identifier
    decision_event_id: Identifier | None = None
    source_references: tuple[Identifier, ...] = Field(min_length=1)
    matching_basis: Literal["explicit_identifiers_only"] = "explicit_identifiers_only"
    record_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    safety_flags: TraceabilitySafetyFlagsV2 = Field(
        default_factory=TraceabilitySafetyFlagsV2
    )


class CorrelationQuarantineV2(FrozenContract):
    schema_version: Literal["runtime_integrity_traceability_quarantine_v2"] = (
        "runtime_integrity_traceability_quarantine_v2"
    )
    correlation_id: str | None
    reason: str
    source_indexes: tuple[int, ...]
    missing_fields: tuple[str, ...] = ()
    ambiguous_fields: tuple[str, ...] = ()
    duplicate_fields: tuple[str, ...] = ()
    timestamp_only_matching_rejected: bool = False
    ids_synthesized: Literal[False] = False
    safety_flags: TraceabilitySafetyFlagsV2 = Field(
        default_factory=TraceabilitySafetyFlagsV2
    )


class CorrelationLedgerReportV2(FrozenContract):
    schema_version: Literal["runtime_integrity_traceability_v2"] = SCHEMA_VERSION
    status: Literal["ok", "warning", "blocked"]
    reason: str
    generated_at_utc: str
    input_event_count: int = Field(ge=0)
    complete_chain_count: int = Field(ge=0)
    quarantine_count: int = Field(ge=0)
    timestamp_only_rejection_count: int = Field(ge=0)
    duplicate_identifier_count: int = Field(ge=0)
    ids_synthesized_count: Literal[0] = 0
    records: tuple[CorrelationRecordV2, ...]
    quarantine: tuple[CorrelationQuarantineV2, ...]
    paper_only: Literal[True] = True
    shadow_only: Literal[True] = True
    research_only: Literal[True] = True
    operational_authority: Literal[False] = False
    live_trading_enabled: Literal[False] = False
    canary_release_allowed: Literal[False] = False
    order_submission_enabled: Literal[False] = False
    real_order_submission_enabled: Literal[False] = False
    exchange_private_access: Literal[False] = False
    sends_orders: Literal[False] = False
    changes_risk: Literal[False] = False
    automatic_promotion_allowed: Literal[False] = False
    publishes_active_signals: Literal[False] = False
    writes_financial_ledger: Literal[False] = False
    safety_flags: TraceabilitySafetyFlagsV2 = Field(
        default_factory=TraceabilitySafetyFlagsV2
    )


def build_correlation_ledger(
    events: Sequence[CorrelationSourceEventV2 | Mapping[str, Any]],
    *,
    generated_at_utc: str | None = None,
) -> CorrelationLedgerReportV2:
    parsed: list[tuple[int, CorrelationSourceEventV2]] = []
    quarantine: list[CorrelationQuarantineV2] = []

    for index, raw in enumerate(events):
        try:
            event = (
                raw
                if isinstance(raw, CorrelationSourceEventV2)
                else CorrelationSourceEventV2.model_validate(raw)
            )
        except (TypeError, ValueError):
            quarantine.append(
                CorrelationQuarantineV2(
                    correlation_id=None,
                    reason="invalid_source_event",
                    source_indexes=(index,),
                )
            )
            continue
        if event.match_method.lower() in TIMESTAMP_ONLY_METHODS:
            quarantine.append(
                CorrelationQuarantineV2(
                    correlation_id=event.correlation_id,
                    reason="timestamp_only_matching_forbidden",
                    source_indexes=(index,),
                    timestamp_only_matching_rejected=True,
                )
            )
            continue
        if event.correlation_id is None:
            quarantine.append(
                CorrelationQuarantineV2(
                    correlation_id=None,
                    reason="missing_explicit_correlation_id",
                    source_indexes=(index,),
                    missing_fields=("correlation_id",),
                )
            )
            continue
        parsed.append((index, event))

    grouped: dict[str, list[tuple[int, CorrelationSourceEventV2]]] = defaultdict(list)
    for item in parsed:
        grouped[item[1].correlation_id or ""].append(item)

    candidates: list[tuple[CorrelationRecordV2, tuple[int, ...]]] = []
    for correlation_id in sorted(grouped):
        group = grouped[correlation_id]
        source_indexes = tuple(index for index, _event in group)
        source_type_counts: dict[str, int] = defaultdict(int)
        for _index, event in group:
            source_type_counts[event.source_type] += 1
        duplicate_types = tuple(
            sorted(name for name, count in source_type_counts.items() if count > 1)
        )
        if duplicate_types:
            quarantine.append(
                CorrelationQuarantineV2(
                    correlation_id=correlation_id,
                    reason="duplicate_source_event_type",
                    source_indexes=source_indexes,
                    duplicate_fields=duplicate_types,
                )
            )
            continue

        missing_source_types = tuple(
            source_type
            for source_type in SOURCE_TYPES
            if source_type_counts[source_type] == 0
        )
        if missing_source_types:
            quarantine.append(
                CorrelationQuarantineV2(
                    correlation_id=correlation_id,
                    reason="incomplete_source_event_chain",
                    source_indexes=source_indexes,
                    missing_fields=missing_source_types,
                )
            )
            continue

        values = _identifier_values(group)
        ambiguous = tuple(
            sorted(name for name, observed in values.items() if len(observed) > 1)
        )
        if ambiguous:
            quarantine.append(
                CorrelationQuarantineV2(
                    correlation_id=correlation_id,
                    reason="ambiguous_identifier_chain",
                    source_indexes=source_indexes,
                    ambiguous_fields=ambiguous,
                )
            )
            continue

        flattened = {
            name: next(iter(observed)) if observed else None
            for name, observed in values.items()
        }
        missing = tuple(
            name
            for name in REQUIRED_IDENTIFIERS
            if flattened.get(name) in {None, ()}
        )
        if missing:
            quarantine.append(
                CorrelationQuarantineV2(
                    correlation_id=correlation_id,
                    reason="incomplete_identifier_chain",
                    source_indexes=source_indexes,
                    missing_fields=missing,
                )
            )
            continue

        source_references = tuple(
            sorted({event.source_reference for _index, event in group})
        )
        record_payload = {
            "correlation_id": correlation_id,
            **flattened,
            "source_references": source_references,
        }
        record = CorrelationRecordV2(
            **record_payload,
            record_sha256=_record_sha256(record_payload),
        )
        candidates.append((record, source_indexes))

    records, duplicate_quarantine = _block_cross_chain_duplicates(candidates)
    quarantine.extend(duplicate_quarantine)
    ordered_quarantine = tuple(
        sorted(
            quarantine,
            key=lambda item: (
                item.correlation_id or "",
                item.reason,
                item.source_indexes,
            ),
        )
    )
    timestamp_rejections = sum(
        item.timestamp_only_matching_rejected for item in ordered_quarantine
    )
    duplicate_count = sum(bool(item.duplicate_fields) for item in ordered_quarantine)
    if records and not ordered_quarantine:
        status, reason = "ok", "all_identifier_chains_complete"
    elif records:
        status, reason = "warning", "partial_identifier_chains_quarantined"
    else:
        status, reason = "blocked", "no_complete_identifier_chain"

    return CorrelationLedgerReportV2(
        status=status,
        reason=reason,
        generated_at_utc=generated_at_utc or _utc_now(),
        input_event_count=len(events),
        complete_chain_count=len(records),
        quarantine_count=len(ordered_quarantine),
        timestamp_only_rejection_count=timestamp_rejections,
        duplicate_identifier_count=duplicate_count,
        records=tuple(records),
        quarantine=ordered_quarantine,
    )


def write_correlation_ledger_report(
    report: CorrelationLedgerReportV2,
    path: str | Path,
    *,
    policy: AtomicWritePolicy | None = None,
) -> AtomicWriteResult:
    return atomic_write_json(
        path,
        report.model_dump(mode="json"),
        policy=policy,
    )


def _identifier_values(
    group: Sequence[tuple[int, CorrelationSourceEventV2]],
) -> dict[str, set[Any]]:
    result: dict[str, set[Any]] = {
        name: set()
        for name in (
            *REQUIRED_IDENTIFIERS,
            "decision_event_id",
        )
    }
    order_ids: set[tuple[str, ...]] = set()
    for _index, event in group:
        for field_name in result:
            if field_name == "order_ids":
                continue
            value = getattr(event, field_name)
            if value is not None:
                result[field_name].add(value)
        if event.order_ids:
            order_ids.add(tuple(sorted(event.order_ids)))
    result["order_ids"] = order_ids
    return result


def _block_cross_chain_duplicates(
    candidates: Sequence[tuple[CorrelationRecordV2, tuple[int, ...]]],
) -> tuple[list[CorrelationRecordV2], list[CorrelationQuarantineV2]]:
    owners: dict[tuple[str, str], set[str]] = defaultdict(set)
    for record, _indexes in candidates:
        for field_name in UNIQUE_IDENTIFIERS:
            value = getattr(record, field_name)
            values = value if field_name == "order_ids" else (value,)
            for item in values:
                owners[(field_name, str(item))].add(record.correlation_id)

    duplicate_by_correlation: dict[str, set[str]] = defaultdict(set)
    for (field_name, _value), correlations in owners.items():
        if len(correlations) > 1:
            for correlation_id in correlations:
                duplicate_by_correlation[correlation_id].add(field_name)

    records: list[CorrelationRecordV2] = []
    quarantine: list[CorrelationQuarantineV2] = []
    for record, indexes in candidates:
        duplicate_fields = tuple(sorted(duplicate_by_correlation[record.correlation_id]))
        if duplicate_fields:
            quarantine.append(
                CorrelationQuarantineV2(
                    correlation_id=record.correlation_id,
                    reason="duplicate_identifier_across_chains",
                    source_indexes=indexes,
                    duplicate_fields=duplicate_fields,
                )
            )
        else:
            records.append(record)
    return records, quarantine


def _record_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
