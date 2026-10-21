"""Immutable complementary evidence, without changing Decision Ledger V4.2."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, field_validator, model_validator

from smartcrypto.execution.decision_ledger_v4_2.contracts import (
    DecisionRecordV42,
    Identifier,
    Sha256Hex,
    TradeLinkRecordV42,
    require_utc_timestamp,
)

SCHEMA: Literal["paper_pretrade_causal_capture_v1"] = "paper_pretrade_causal_capture_v1"
Stage = Literal[
    "generated", "selector_rejected", "risk_rejected", "allow", "trade_linked", "outcome"
]
FeatureName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
Reason = Literal[
    "CLOCK_UNPROVEN",
    "CLOCK_SKEW",
    "CLOCK_REGRESSION",
    "SOURCE_MISSING",
    "SOURCE_UNTRUSTED",
    "SOURCE_FUTURE",
    "CANDLE_NOT_CLOSED",
    "SOURCE_STALE",
    "VECTOR_INCOMPLETE",
    "LATE_CAPTURE",
    "DECISION_FUTURE",
    "IDENTITY_MISMATCH",
]


def digest(value: object) -> str:
    raw = json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    @field_validator(
        "event_time_utc",
        "available_at_utc",
        "candle_closed_at_utc",
        "captured_at_utc",
        "decision_timestamp_utc",
        "observed_at_utc",
        "close_time_utc",
        "measured_at_utc",
        "valid_until_utc",
        check_fields=False,
    )
    @classmethod
    def utc(cls, value: datetime | None) -> datetime | None:
        return require_utc_timestamp(value) if value is not None else None


class ClockProof(Frozen):
    reference_sha256: Sha256Hex
    measured_at_utc: datetime
    valid_until_utc: datetime
    error_bound_seconds: Annotated[StrictFloat, Field(ge=0, le=1)]
    synthetic: bool

    @model_validator(mode="after")
    def interval(self) -> Self:
        if self.valid_until_utc < self.measured_at_utc:
            raise ValueError("clock_proof_interval_invalid")
        return self


class SourceReceipt(Frozen):
    source_id: Identifier
    source_sha256: Sha256Hex
    receipt_id: Sha256Hex
    event_time_utc: datetime
    available_at_utc: datetime
    received_monotonic_ns: Annotated[int, Field(ge=0)]
    availability_basis: Literal["LOCAL_BOUNDARY_RECEIPT"]
    candle_closed_at_utc: datetime | None = None
    is_candle: bool = False

    @model_validator(mode="after")
    def receipt_seal(self) -> Self:
        body = self.model_dump(exclude={"receipt_id"})
        normalized = {k: v.isoformat() if isinstance(v, datetime) else v for k, v in body.items()}
        if self.receipt_id != digest(normalized):
            raise ValueError("source_receipt_seal_mismatch")
        return self


class CaptureBody(Frozen):
    schema_version: Literal["paper_pretrade_causal_capture_v1"] = SCHEMA
    stage: Literal["generated"] = "generated"
    epoch_id: Identifier
    opportunity_id: Identifier
    candidate_id: Identifier | None = None
    signal_id: Identifier | None = None
    boundary: Literal["model_input", "selector_input"]
    symbol: Annotated[str, Field(pattern=r"^[A-Z0-9]{3,24}$")]
    side: Literal["long", "short"]
    feature_contract_version: Identifier
    feature_names: Annotated[tuple[FeatureName, ...], Field(min_length=1, max_length=128)]
    feature_values: Annotated[tuple[StrictFloat, ...], Field(max_length=128)]
    feature_sources: Annotated[
        tuple[Annotated[tuple[Identifier, ...], Field(max_length=128)], ...], Field(max_length=128)
    ]
    vector_sha256: Sha256Hex
    sources: Annotated[tuple[SourceReceipt, ...], Field(max_length=128)]
    captured_at_utc: datetime
    captured_monotonic_ns: Annotated[int, Field(ge=0)]
    clock_proof: ClockProof | None
    model_id: Identifier
    model_sha256: Sha256Hex
    configuration_sha256: Sha256Hex
    synthetic: bool
    eligibility: Literal["CAPTURED_PENDING_DECISION", "QUARANTINED"]
    quarantine_reasons: tuple[Reason, ...]

    @model_validator(mode="after")
    def vector_integrity(self) -> Self:
        if len(set(self.feature_names)) != len(self.feature_names):
            raise ValueError("duplicate_feature_name")
        if any(
            re.search(r"(?:password|secret|token|credential|api_key)", n)
            for n in self.feature_names
        ):
            raise ValueError("sensitive_feature_name_forbidden")
        forbidden = {
            "pnl",
            "net_pnl",
            "gross_pnl",
            "close_time",
            "close_time_utc",
            "exit_reason",
            "realized_duration",
            "realized_pnl",
            "mfe",
            "mae",
            "trade_id",
            "outcome",
        }
        if any(n.removeprefix("feature_") in forbidden for n in self.feature_names):
            raise ValueError("post_outcome_feature_forbidden")
        expected = digest(
            {"names": self.feature_names, "float64_hex": [v.hex() for v in self.feature_values]}
        )
        if expected != self.vector_sha256:
            raise ValueError("original_vector_hash_mismatch")
        if len({r.source_id for r in self.sources}) != len(self.sources):
            raise ValueError("duplicate_source_id")
        if (self.eligibility == "QUARANTINED") != bool(self.quarantine_reasons):
            raise ValueError("quarantine_state_inconsistent")
        if not self.quarantine_reasons and (
            len(self.feature_values) != len(self.feature_names)
            or len(self.feature_sources) != len(self.feature_names)
        ):
            raise ValueError("incomplete_vector_cannot_be_eligible")
        if self.clock_proof is not None and self.clock_proof.synthetic and not self.synthetic:
            raise ValueError("synthetic_clock_cannot_authorize_real_evidence")
        return self


class CaptureRecord(CaptureBody):
    record_sha256: Sha256Hex

    @model_validator(mode="after")
    def seal(self) -> Self:
        if self.record_sha256 != digest(self.model_dump(mode="json", exclude={"record_sha256"})):
            raise ValueError("capture_seal_mismatch")
        return self


class LifecycleBody(Frozen):
    schema_version: Literal["paper_pretrade_causal_capture_v1"] = SCHEMA
    stage: Literal["selector_rejected", "risk_rejected", "allow", "trade_linked", "outcome"]
    epoch_id: Identifier
    opportunity_id: Identifier
    capture_sha256: Sha256Hex
    decision_timestamp_utc: datetime
    observed_at_utc: datetime
    decision: DecisionRecordV42 | None = None
    trade_link: TradeLinkRecordV42 | None = None
    close_time_utc: datetime | None = None
    reported_net_pnl: StrictFloat | None = None
    financial_source_sha256: Sha256Hex | None = None
    synthetic: bool
    eligibility: Literal["CAUSALLY_COMPLETE", "QUARANTINED"]
    quarantine_reasons: tuple[Reason, ...]
    financial_certified: Literal[False] = False

    @model_validator(mode="after")
    def required_links(self) -> Self:
        if (self.eligibility == "QUARANTINED") != bool(self.quarantine_reasons):
            raise ValueError("quarantine_state_inconsistent")
        if self.stage != "selector_rejected" and self.decision is None:
            raise ValueError("sealed_decision_required")
        if self.stage == "selector_rejected" and self.decision is not None:
            raise ValueError("selector_rejection_cannot_override_decision")
        if self.decision is not None:
            if self.decision_timestamp_utc != self.decision.decision_timestamp:
                raise ValueError("decision_timestamp_identity_mismatch")
            expected = "BLOCK" if self.stage == "risk_rejected" else "ALLOW"
            if self.decision.final_decision.value != expected:
                raise ValueError("decision_stage_mismatch")
            if self.stage == "risk_rejected" and self.decision.risk_decision.value not in {
                "REJECTED",
                "ERROR",
            }:
                raise ValueError("risk_rejection_not_observed")
        if self.stage in {"trade_linked", "outcome"} and self.trade_link is None:
            raise ValueError("sealed_trade_link_required")
        if self.stage not in {"trade_linked", "outcome"} and self.trade_link is not None:
            raise ValueError("trade_link_forbidden_before_link_stage")
        if self.stage == "outcome":
            if (
                self.close_time_utc is None
                or self.reported_net_pnl is None
                or self.financial_source_sha256 is None
            ):
                raise ValueError("outcome_source_required")
        elif any(
            v is not None
            for v in (self.close_time_utc, self.reported_net_pnl, self.financial_source_sha256)
        ):
            raise ValueError("outcome_fields_forbidden_before_outcome")
        return self


class LifecycleRecord(LifecycleBody):
    record_sha256: Sha256Hex

    @model_validator(mode="after")
    def seal(self) -> Self:
        if self.record_sha256 != digest(self.model_dump(mode="json", exclude={"record_sha256"})):
            raise ValueError("lifecycle_seal_mismatch")
        return self


Evidence = CaptureRecord | LifecycleRecord


def lineage_aliases(record: Evidence) -> tuple[tuple[str, str], ...]:
    aliases: list[tuple[str, str]] = []
    if isinstance(record, CaptureRecord):
        aliases.extend(
            (key, value)
            for key in ("candidate_id", "signal_id")
            if (value := getattr(record, key)) is not None
        )
    elif record.decision is not None:
        aliases.extend(
            (key, getattr(record.decision, key))
            for key in ("candidate_id", "signal_id", "event_id")
        )
        if record.trade_link is not None:
            aliases.append(
                ("trade_id", digest([record.trade_link.pair, record.trade_link.trade_id]))
            )
    return tuple(aliases)


def decision_identity_matches(capture: CaptureRecord, decision: DecisionRecordV42) -> bool:
    return (
        capture.candidate_id == decision.candidate_id
        and capture.signal_id == decision.signal_id
        and capture.symbol == decision.symbol
        and capture.side == decision.side.value
        and capture.model_id == decision.model_id
        and capture.model_sha256 == decision.model_hash
        and capture.feature_contract_version == decision.feature_contract_version
    )


def trade_identity_matches(link: TradeLinkRecordV42, decision: DecisionRecordV42) -> bool:
    return (
        link.parent_event_id == decision.event_id
        and link.decision_payload_sha256 == decision.payload_sha256
        and all(
            getattr(link, key) == getattr(decision, key)
            for key in (
                "signal_id",
                "candidate_id",
                "correlation_id",
                "pair",
                "symbol",
                "side",
                "decision_timestamp",
            )
        )
    )


def seal_capture(body: CaptureBody) -> CaptureRecord:
    data = body.model_dump(mode="json")
    return CaptureRecord.model_validate({**data, "record_sha256": digest(data)})


def seal_lifecycle(body: LifecycleBody) -> LifecycleRecord:
    data = body.model_dump(mode="json")
    return LifecycleRecord.model_validate({**data, "record_sha256": digest(data)})


def parse_record(value: object) -> Evidence:
    if not isinstance(value, dict):
        raise ValueError("evidence_object_required")
    cls = CaptureRecord if value.get("stage") == "generated" else LifecycleRecord
    return cls.model_validate(value)
