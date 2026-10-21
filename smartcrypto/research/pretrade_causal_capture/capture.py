"""Bounded in-memory source API. Never calls a writer, model, or trading code."""

from __future__ import annotations

import hashlib
import logging
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from queue import Empty, Full, Queue
from typing import Annotated, Literal, Protocol, TypedDict

from pydantic import Field, model_validator

from smartcrypto.execution.decision_ledger_v4_2.contracts import (
    DecisionRecordV42,
    Identifier,
    TradeLinkRecordV42,
)

from .contracts import (
    CaptureBody,
    CaptureRecord,
    ClockProof,
    Evidence,
    Frozen,
    LifecycleBody,
    LifecycleRecord,
    Reason,
    SourceReceipt,
    decision_identity_matches,
    digest,
    lineage_aliases,
    parse_record,
    seal_capture,
    seal_lifecycle,
    trade_identity_matches,
)

LOGGER = logging.getLogger(__name__)


class SessionReport(TypedDict):
    enabled: bool
    runtime_integration: Literal["BLOCKED_FOR_RUNTIME_INTEGRATION"]
    status: Literal["blocked", "disabled", "harness_only"]
    evidence_valid: bool
    metrics: dict[str, int]
    quarantined_records: int
    queued_records: int
    write_performed: Literal[False]
    operational_authority: Literal[False]
    real_prospective_collection_proven: Literal[False]


class CaptureConfig(Frozen):
    enabled: bool = False
    epoch_id: Identifier = "unconfigured"
    synthetic: bool = True
    queue_records: Annotated[int, Field(ge=1, le=256)] = 64
    max_opportunities: Annotated[int, Field(ge=1, le=4096)] = 1024
    max_sources: Annotated[int, Field(ge=1, le=512)] = 128
    max_record_bytes: Annotated[int, Field(ge=1024, le=65536)] = 65536
    max_source_bytes: Annotated[int, Field(ge=1, le=65536)] = 65536
    max_source_age_seconds: Annotated[float, Field(gt=0, le=3600)] = 300.0
    max_clock_error_seconds: Annotated[float, Field(ge=0, le=1)] = 0.05

    @model_validator(mode="after")
    def explicit_epoch(self) -> CaptureConfig:
        if self.enabled and self.epoch_id == "unconfigured":
            raise ValueError("explicit_epoch_required")
        return self


@dataclass(frozen=True)
class Tick:
    utc: datetime
    monotonic_ns: int


class Clock(Protocol):
    def now(self) -> Tick:
        """Sample UTC and monotonic time at the actual source boundary."""


class LocalClock:
    def now(self) -> Tick:
        return Tick(datetime.now(timezone.utc), time.monotonic_ns())


def causal_reasons(
    capture: CaptureRecord, at: datetime | None, config: CaptureConfig
) -> tuple[Reason, ...]:
    reasons: list[Reason] = list(capture.quarantine_reasons)
    proof = capture.clock_proof
    if (
        proof is None
        or not proof.measured_at_utc <= capture.captured_at_utc <= proof.valid_until_utc
    ):
        reasons.append("CLOCK_UNPROVEN")
    elif proof.error_bound_seconds > config.max_clock_error_seconds:
        reasons.append("CLOCK_SKEW")
    bound = timedelta(seconds=proof.error_bound_seconds if proof else 0)
    if at is not None and capture.captured_at_utc + bound > at:
        reasons.append("LATE_CAPTURE")
    if (
        at is not None
        and proof is not None
        and not proof.measured_at_utc <= at <= proof.valid_until_utc
    ):
        reasons.append("CLOCK_UNPROVEN")
    if len(capture.feature_values) != len(capture.feature_names) or len(
        capture.feature_sources
    ) != len(capture.feature_names):
        reasons.append("VECTOR_INCOMPLETE")
    sources = {r.source_id: r for r in capture.sources}
    for required in capture.feature_sources:
        if not required or any(name not in sources for name in required):
            reasons.append("SOURCE_MISSING")
    for source in capture.sources:
        if (
            proof is None
            or not proof.measured_at_utc <= source.available_at_utc <= proof.valid_until_utc
        ):
            reasons.append("CLOCK_UNPROVEN")
        if (
            source.available_at_utc + bound > capture.captured_at_utc
            or source.received_monotonic_ns > capture.captured_monotonic_ns
        ):
            reasons.append("SOURCE_FUTURE")
        if source.event_time_utc > source.available_at_utc:
            reasons.append("SOURCE_FUTURE")
        if source.is_candle and (
            source.candle_closed_at_utc is None
            or source.candle_closed_at_utc > source.available_at_utc
        ):
            reasons.append("CANDLE_NOT_CLOSED")
        if (
            capture.captured_at_utc - source.available_at_utc
        ).total_seconds() > config.max_source_age_seconds:
            reasons.append("SOURCE_STALE")
    return tuple(sorted(set(reasons)))


class CaptureSession:
    """Single producer owner; a separate harness drains the bounded mailbox.

    The receipts must be minted at actual source delivery. This class is NOT
    connected to the installed producer and does not prove host clock custody.
    """

    def __init__(
        self,
        config: CaptureConfig | None = None,
        *,
        clock: Clock | None = None,
        proof: ClockProof | None = None,
    ) -> None:
        self.config = config or CaptureConfig()
        self.clock = clock or LocalClock()
        self.proof = proof
        self.metrics: Counter[str] = Counter()
        self._queue: Queue[Evidence] = Queue(maxsize=self.config.queue_records)
        self._records: dict[str, Evidence] = {}
        self._receipts: dict[str, SourceReceipt] = {}
        self._aliases: dict[tuple[str, str, str], str] = {}
        self._last_tick: Tick | None = None
        self._clock_fault = False

    def fail(self, code: str) -> None:
        # Only internal codes reach this method; never log source bytes or exception values.
        self.metrics[code] += 1
        LOGGER.warning("pretrade_evidence_unusable reason=%s", code)

    def _tick(self) -> Tick:
        tick = self.clock.now()
        if tick.utc.utcoffset() != timedelta(0) or tick.monotonic_ns < 0:
            raise ValueError("clock_sample_invalid")
        if self._last_tick and (
            tick.utc < self._last_tick.utc or tick.monotonic_ns <= self._last_tick.monotonic_ns
        ):
            self._clock_fault = True
        self._last_tick = tick
        return tick

    def receive_source(
        self,
        *,
        source_id: str,
        payload: bytes,
        event_time_utc: datetime,
        is_candle: bool = False,
        candle_closed_at_utc: datetime | None = None,
    ) -> SourceReceipt | None:
        if not self.config.enabled:
            return None
        try:
            if (
                len(payload) > self.config.max_source_bytes
                or len(self._receipts) >= self.config.max_sources
            ):
                self.fail("source_budget_exceeded")
                return None
            tick = self._tick()
            body = dict(
                source_id=source_id,
                source_sha256=hashlib.sha256(payload).hexdigest(),
                event_time_utc=event_time_utc,
                available_at_utc=tick.utc,
                received_monotonic_ns=tick.monotonic_ns,
                availability_basis="LOCAL_BOUNDARY_RECEIPT",
                is_candle=is_candle,
                candle_closed_at_utc=candle_closed_at_utc,
            )
            normalized = {
                k: v.isoformat() if isinstance(v, datetime) else v for k, v in body.items()
            }
            receipt = SourceReceipt.model_validate({**body, "receipt_id": digest(normalized)})
            self._receipts[receipt.receipt_id] = receipt
            return receipt
        except (ValueError, TypeError, OverflowError):
            self.fail("invalid_source_receipt")
            return None

    def _emit(self, record: Evidence) -> bool:
        record = parse_record(record.model_dump(mode="json"))
        stage = (
            "decision"
            if record.stage in {"allow", "risk_rejected", "selector_rejected"}
            else record.stage
        )
        key = digest([record.epoch_id, record.opportunity_id, stage])
        previous = self._records.get(key)
        if previous is not None:
            if previous.record_sha256 == record.record_sha256:
                self.metrics["idempotent"] += 1
                return True
            self.fail("identity_conflict")
            return False
        aliases = [(record.epoch_id, kind, value) for kind, value in lineage_aliases(record)]
        if any(
            self._aliases.get(alias, record.opportunity_id) != record.opportunity_id
            for alias in aliases
        ):
            self.fail("lineage_identity_conflict")
            return False
        if len(self._records) >= self.config.max_opportunities * 4:
            self.fail("record_budget_exceeded")
            return False
        if len(record.model_dump_json().encode()) > self.config.max_record_bytes:
            self.fail("record_byte_budget_exceeded")
            return False
        try:
            self._queue.put_nowait(record)
        except Full:
            self.fail("queue_saturated")
            return False
        self._records[key] = record
        self._aliases.update((alias, record.opportunity_id) for alias in aliases)
        return True

    def capture(
        self,
        *,
        opportunity_id: str,
        symbol: str,
        side: Literal["long", "short"],
        feature_names: tuple[str, ...],
        feature_values: tuple[float, ...],
        feature_sources: tuple[tuple[str, ...], ...],
        sources: tuple[SourceReceipt, ...],
        feature_contract_version: str,
        model_id: str,
        model_sha256: str,
        configuration_sha256: str,
        candidate_id: str | None = None,
        signal_id: str | None = None,
        boundary: Literal["model_input", "selector_input"] = "model_input",
    ) -> CaptureRecord | None:
        if not self.config.enabled:
            return None
        try:
            if (
                len(feature_names) > 128
                or len(feature_values) > 128
                or len(sources) > 128
                or len(feature_sources) > 128
                or any(len(ids) > 128 for ids in feature_sources)
                or any(len(name) > 64 for name in feature_names)
            ):
                self.fail("capture_shape_budget_exceeded")
                return None
            if (
                sum(isinstance(r, CaptureRecord) for r in self._records.values())
                >= self.config.max_opportunities
            ):
                self.fail("opportunity_budget_exceeded")
                return None
            tick = self._tick()
            reasons: list[Reason] = []
            if len(feature_values) != len(feature_names) or len(feature_sources) != len(
                feature_names
            ):
                reasons.append("VECTOR_INCOMPLETE")
            if self._clock_fault:
                reasons.append("CLOCK_REGRESSION")
            if any(self._receipts.get(r.receipt_id) != r for r in sources):
                reasons.append("SOURCE_UNTRUSTED")
            # The original float64 bits, including signed zero, determine identity.
            vector_sha = digest(
                {"names": feature_names, "float64_hex": [v.hex() for v in feature_values]}
            )
            body = CaptureBody(
                epoch_id=self.config.epoch_id,
                opportunity_id=opportunity_id,
                candidate_id=candidate_id,
                signal_id=signal_id,
                boundary=boundary,
                symbol=symbol,
                side=side,
                feature_contract_version=feature_contract_version,
                feature_names=feature_names,
                feature_values=feature_values,
                feature_sources=feature_sources,
                sources=sources,
                vector_sha256=vector_sha,
                captured_at_utc=tick.utc,
                captured_monotonic_ns=tick.monotonic_ns,
                clock_proof=self.proof,
                model_id=model_id,
                model_sha256=model_sha256,
                configuration_sha256=configuration_sha256,
                synthetic=self.config.synthetic,
                eligibility="QUARANTINED" if reasons else "CAPTURED_PENDING_DECISION",
                quarantine_reasons=tuple(reasons),
            )
            preliminary = seal_capture(body)
            reasons = list(causal_reasons(preliminary, None, self.config))
            body = CaptureBody.model_validate(
                {
                    **body.model_dump(),
                    "eligibility": "QUARANTINED" if reasons else "CAPTURED_PENDING_DECISION",
                    "quarantine_reasons": reasons,
                }
            )
            record = seal_capture(body)
            return record if self._emit(record) else None
        except (ValueError, TypeError, AttributeError, OverflowError):
            self.fail("invalid_capture_contract")
            return None

    def transition(
        self,
        capture: CaptureRecord,
        *,
        decision: DecisionRecordV42 | None = None,
        selector_rejected: bool = False,
        trade_link: TradeLinkRecordV42 | None = None,
        close_time_utc: datetime | None = None,
        reported_net_pnl: float | None = None,
        financial_source_sha256: str | None = None,
    ) -> LifecycleRecord | None:
        if not self.config.enabled:
            return None
        try:
            known = self._records.get(
                digest([self.config.epoch_id, capture.opportunity_id, "generated"])
            )
            if known is None or known.record_sha256 != capture.record_sha256:
                self.fail("capture_parent_missing")
                return None
            tick = self._tick()
            if decision is not None:
                decision = DecisionRecordV42.model_validate(decision.model_dump(mode="json"))
            if trade_link is not None:
                trade_link = TradeLinkRecordV42.model_validate(trade_link.model_dump(mode="json"))
            at = decision.decision_timestamp if decision else tick.utc
            reasons = list(causal_reasons(capture, at, self.config))
            if at > tick.utc:
                reasons.append("DECISION_FUTURE")
            if self._clock_fault:
                reasons.append("CLOCK_REGRESSION")
            if decision and not decision_identity_matches(capture, decision):
                reasons.append("IDENTITY_MISMATCH")
            stage: Literal["selector_rejected", "risk_rejected", "allow", "trade_linked", "outcome"]
            stage = (
                "selector_rejected"
                if selector_rejected
                else "allow"
                if decision and decision.final_decision.value == "ALLOW"
                else "risk_rejected"
            )
            if selector_rejected and decision is not None:
                raise ValueError("selector_rejection_cannot_override_decision")
            if trade_link is not None:
                parent = self._records.get(
                    digest([capture.epoch_id, capture.opportunity_id, "decision"])
                )
                if (
                    not isinstance(parent, LifecycleRecord)
                    or parent.decision is None
                    or parent.stage != "allow"
                ):
                    raise ValueError("allow_parent_required")
                if decision != parent.decision:
                    reasons.append("IDENTITY_MISMATCH")
                if decision is None:
                    raise ValueError("exact_decision_required")
                if not trade_identity_matches(trade_link, decision):
                    reasons.append("IDENTITY_MISMATCH")
                if trade_link.execution_timestamp > tick.utc or parent.observed_at_utc > tick.utc:
                    reasons.append("DECISION_FUTURE")
                stage = "trade_linked"
            if close_time_utc is not None:
                linked = self._records.get(
                    digest([capture.epoch_id, capture.opportunity_id, "trade_linked"])
                )
                if not isinstance(linked, LifecycleRecord) or linked.trade_link != trade_link:
                    raise ValueError("exact_trade_link_parent_required")
                if trade_link is None:
                    raise ValueError("exact_trade_link_required")
                if not trade_link.execution_timestamp <= close_time_utc <= tick.utc:
                    reasons.append("DECISION_FUTURE")
                stage = "outcome"
            body = LifecycleBody(
                stage=stage,
                epoch_id=capture.epoch_id,
                opportunity_id=capture.opportunity_id,
                capture_sha256=capture.record_sha256,
                decision_timestamp_utc=at,
                observed_at_utc=tick.utc,
                decision=decision,
                trade_link=trade_link,
                close_time_utc=close_time_utc,
                reported_net_pnl=reported_net_pnl,
                financial_source_sha256=financial_source_sha256,
                synthetic=capture.synthetic,
                eligibility="QUARANTINED" if reasons else "CAUSALLY_COMPLETE",
                quarantine_reasons=tuple(sorted(set(reasons))),
            )
            record = seal_lifecycle(body)
            return record if self._emit(record) else None
        except (ValueError, TypeError, AssertionError, OverflowError):
            self.fail("invalid_lifecycle_contract")
            return None

    def take_pending(self) -> Evidence | None:
        try:
            return self._queue.get_nowait()
        except Empty:
            return None

    def records(self) -> tuple[Evidence, ...]:
        return tuple(self._records.values())

    def report(self) -> SessionReport:
        failures = {k: v for k, v in self.metrics.items() if k != "idempotent"}
        quarantined = sum(r.eligibility == "QUARANTINED" for r in self._records.values())
        return {
            "enabled": self.config.enabled,
            "runtime_integration": "BLOCKED_FOR_RUNTIME_INTEGRATION",
            "status": "blocked"
            if failures or quarantined
            else "disabled"
            if not self.config.enabled
            else "harness_only",
            "evidence_valid": not bool(failures or quarantined),
            "metrics": dict(self.metrics),
            "quarantined_records": quarantined,
            "queued_records": self._queue.qsize(),
            "write_performed": False,
            "operational_authority": False,
            "real_prospective_collection_proven": False,
        }
