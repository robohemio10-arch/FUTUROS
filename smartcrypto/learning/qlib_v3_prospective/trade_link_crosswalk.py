"""Exact ex-ante crosswalk between operational and V3 shadow decisions.

The crosswalk is research-only metadata persisted inside an admitted V3 signal.
It never alters the operational signal, RiskManager result, strategy, stake,
leverage, order submission, exchange access, or model registry.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from smartcrypto.execution.decision_ledger_v4_2.contracts import (
    DecisionRecordV42,
    FinalDecision,
)

from .contracts import EvidenceError, digest

SCHEMA_VERSION = "qlib_v3_operational_decision_crosswalk_v1"
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_BODY_FIELDS = {
    "schema_version",
    "signal_id",
    "operational_decision_event_id",
    "operational_decision_payload_sha256",
    "v3_decision_event_id",
    "v3_decision_payload_sha256",
}
_ALL_FIELDS = _BODY_FIELDS | {"crosswalk_sha256"}


def _same_signal_occurrence(
    operational_record: DecisionRecordV42,
    v3_decision: DecisionRecordV42,
) -> bool:
    return (
        operational_record.signal_id == v3_decision.signal_id
        and operational_record.candidate_id == v3_decision.candidate_id
        and operational_record.correlation_id == v3_decision.correlation_id
        and operational_record.pair == v3_decision.pair
        and operational_record.symbol == v3_decision.symbol
        and operational_record.side == v3_decision.side
    )


def seal_operational_crosswalk(
    *,
    operational_record: DecisionRecordV42,
    v3_decision: DecisionRecordV42,
) -> dict[str, str]:
    """Seal the exact dual-lineage relation observed before Paper publication."""

    if (
        operational_record.final_decision is not FinalDecision.ALLOW
        or v3_decision.final_decision is not FinalDecision.ALLOW
    ):
        raise EvidenceError("operational_crosswalk_requires_allow_decisions")

    if not _same_signal_occurrence(operational_record, v3_decision):
        raise EvidenceError("operational_crosswalk_signal_identity_mismatch")

    body = {
        "schema_version": SCHEMA_VERSION,
        "signal_id": v3_decision.signal_id,
        "operational_decision_event_id": operational_record.event_id,
        "operational_decision_payload_sha256": operational_record.payload_sha256,
        "v3_decision_event_id": v3_decision.event_id,
        "v3_decision_payload_sha256": v3_decision.payload_sha256,
    }
    return body | {"crosswalk_sha256": digest(body)}


def validate_operational_crosswalk(
    value: object,
    *,
    v3_decision: DecisionRecordV42,
) -> dict[str, str] | None:
    """Validate and normalize an optional persisted crosswalk.

    Absence is intentionally accepted for signals admitted before this contract
    existed. Such signals remain valid V3 evidence but cannot acquire a closure
    through an operational decision-event id.
    """

    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise EvidenceError("operational_crosswalk_not_object")

    row = dict(value)
    if set(row) != _ALL_FIELDS:
        raise EvidenceError("operational_crosswalk_fields_invalid")
    if row.get("schema_version") != SCHEMA_VERSION:
        raise EvidenceError("operational_crosswalk_schema_invalid")

    for key in (
        "signal_id",
        "operational_decision_event_id",
        "v3_decision_event_id",
    ):
        item = row.get(key)
        if not isinstance(item, str) or not _IDENTIFIER.fullmatch(item):
            raise EvidenceError(f"operational_crosswalk_identifier_invalid:{key}")

    for key in (
        "operational_decision_payload_sha256",
        "v3_decision_payload_sha256",
        "crosswalk_sha256",
    ):
        item = row.get(key)
        if not isinstance(item, str) or not _SHA256.fullmatch(item):
            raise EvidenceError(f"operational_crosswalk_sha256_invalid:{key}")

    body: dict[str, Any] = {
        key: row[key]
        for key in (
            "schema_version",
            "signal_id",
            "operational_decision_event_id",
            "operational_decision_payload_sha256",
            "v3_decision_event_id",
            "v3_decision_payload_sha256",
        )
    }
    if row["crosswalk_sha256"] != digest(body):
        raise EvidenceError("operational_crosswalk_hash_mismatch")

    if (
        row["signal_id"] != v3_decision.signal_id
        or row["v3_decision_event_id"] != v3_decision.event_id
        or row["v3_decision_payload_sha256"] != v3_decision.payload_sha256
    ):
        raise EvidenceError("operational_crosswalk_v3_identity_mismatch")

    return {
        "schema_version": str(row["schema_version"]),
        "signal_id": str(row["signal_id"]),
        "operational_decision_event_id": str(
            row["operational_decision_event_id"]
        ),
        "operational_decision_payload_sha256": str(
            row["operational_decision_payload_sha256"]
        ),
        "v3_decision_event_id": str(row["v3_decision_event_id"]),
        "v3_decision_payload_sha256": str(
            row["v3_decision_payload_sha256"]
        ),
        "crosswalk_sha256": str(row["crosswalk_sha256"]),
    }
