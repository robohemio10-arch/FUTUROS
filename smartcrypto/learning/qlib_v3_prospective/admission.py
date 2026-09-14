"""Exact sealed-decision and trade-link admission; no scoring or identity inference."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from smartcrypto.execution.decision_ledger_v4_2.contracts import (
    DecisionRecordV42, FinalDecision, TradeLinkRecordV42, parse_payload_record,
)

from .activation import Activation
from .contracts import EvidenceError, check_identity, check_origin, utc


def signal(row: dict[str, Any], activation: Activation, now: datetime) -> dict[str, Any]:
    check_origin(row)
    check_identity(row.get("identity"), activation.identity)
    record = parse_payload_record(row.get("decision", {}))
    if not isinstance(record, DecisionRecordV42):
        raise EvidenceError("sealed_decision_required")
    if record.model_hash != activation.identity.model_artifact_sha256:
        raise EvidenceError("decision_model_mismatch")
    observed = utc(row.get("signal_timestamp_utc"))
    if not activation.boundary <= record.decision_timestamp <= observed <= now:
        raise EvidenceError("signal_or_decision_outside_prospective_window")
    if row.get("signal_id") != record.signal_id or row.get("decision_event_id") != record.event_id:
        raise EvidenceError("signal_decision_identity_mismatch")
    if any(key in row for key in ("net_pnl", "outcome", "close_time_utc", "trade_id")):
        raise EvidenceError("outcome_in_signal_forbidden")
    return {"epoch_version": "v3", "identity": activation.identity.mapping(),
            "origin": "natural_paper_runtime", "replayed": False, "backfilled": False,
            "synthetic": False, "signal_id": record.signal_id, "decision_event_id": record.event_id,
            "signal_timestamp_utc": observed.isoformat(), "decision": record.model_dump(mode="json")}


def outcome(row: dict[str, Any], signals: dict[str, dict[str, Any]],
            activation: Activation, now: datetime) -> dict[str, Any]:
    check_origin(row)
    check_identity(row.get("identity"), activation.identity)
    parent = signals.get(str(row.get("signal_id", "")))
    if parent is None:
        raise EvidenceError("outcome_without_eligible_v3_signal")
    decision = DecisionRecordV42.model_validate(parent["decision"])
    link = parse_payload_record(row.get("trade_link", {}))
    if not isinstance(link, TradeLinkRecordV42):
        raise EvidenceError("sealed_trade_link_required")
    if decision.final_decision != FinalDecision.ALLOW:
        raise EvidenceError("trade_origin_not_allowed")
    exact = (link.parent_event_id == decision.event_id
             and link.signal_id == decision.signal_id
             and link.decision_payload_sha256 == decision.payload_sha256
             and link.candidate_id == decision.candidate_id
             and link.correlation_id == decision.correlation_id
             and link.symbol == decision.symbol and link.side == decision.side
             and link.pair == decision.pair
             and link.decision_timestamp == decision.decision_timestamp
             and row.get("decision_event_id") == decision.event_id
             and type(row.get("trade_id")) is int and row["trade_id"] == link.trade_id)
    if not exact:
        raise EvidenceError("outcome_causal_identity_mismatch")
    opened, closed = utc(row.get("open_time_utc")), utc(row.get("close_time_utc"))
    if not utc(parent["signal_timestamp_utc"]) <= opened == link.execution_timestamp < closed <= now:
        raise EvidenceError("outcome_causal_time_mismatch")
    if row.get("is_closed") is not True:
        raise EvidenceError("outcome_not_closed")
    pnl = row.get("net_pnl")
    if isinstance(pnl, bool) or not isinstance(pnl, (int, float)) or not math.isfinite(pnl):
        raise EvidenceError("outcome_pnl_not_finite")
    return {"epoch_version": "v3", "identity": activation.identity.mapping(),
            "origin": "natural_paper_runtime", "replayed": False, "backfilled": False,
            "synthetic": False, "signal_id": decision.signal_id, "decision_event_id": decision.event_id,
            "trade_id": link.trade_id, "trade_link": link.model_dump(mode="json"),
            "open_time_utc": opened.isoformat(), "close_time_utc": closed.isoformat(),
            "is_closed": True, "net_pnl": pnl}
