"""Ex-ante Paper decision and exact closed-trade exports to the existing V3 store.

This is an opt-in observer at producer boundaries, not a replay/import CLI.
Failure closes evidence admission only; financial publication stays unchanged.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from smartcrypto.execution.decision_ledger_v4_2.contracts import (
    DecisionRecordV42,
    FinalDecision,
    seal_trade_link_record,
)
from smartcrypto.runtime.integrity_traceability_v2 import AtomicWriteError

from . import admission, store
from .activation import Activation, load_activation, read_object, safe_path
from .contracts import CANONICAL, EvidenceError, digest, utc
from .orchestrator import merge

LOGGER = logging.getLogger(__name__)
CONFIG_ENV = "QLIB_V3_NATURAL_EVIDENCE_CONFIG"
ConfigSource = str | Path | Mapping[str, object] | None
# Envelopes follow the existing consumer contract; their sealed nested payloads
# are validated by DecisionRecordV42/TradeLinkRecordV42 before storage.
Envelope = dict[str, Any]


@dataclass(frozen=True)
class ProducerReport:
    status: Literal["disabled", "ok", "blocked"]
    reason: str
    new_signal_count: int = 0
    new_outcome_count: int = 0
    skipped_without_parent: int = 0
    write_requested: bool = False
    write_performed: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "qlib_v3_natural_evidence_producer_wiring_v1",
            "status": self.status, "reason": self.reason,
            "new_signal_count": self.new_signal_count,
            "new_outcome_count": self.new_outcome_count,
            "skipped_without_parent": self.skipped_without_parent,
            "write_requested": self.write_requested,
            "write_performed": self.write_performed,
            "research_only": True, "operational_authority": False,
            "paper_behavior_changed": False, "sends_orders": False,
            "v2_evidence_imported": False, "historical_backfill_allowed": False,
        }


def _activation(root: Path, source: ConfigSource) -> Activation | None:
    if source is None:
        source = os.environ.get(CONFIG_ENV)
    if source is None:
        return None
    if isinstance(source, Mapping):
        config = dict(source)
    elif isinstance(source, (str, Path)):
        config = read_object(root / source)
    else:
        raise EvidenceError("producer_config_invalid")
    if config.get("enabled") is False:
        return None
    if config.get("enabled") is not True:
        raise EvidenceError("producer_enabled_must_be_boolean")
    if set(config) != {"enabled", "activation", "freeze"}:
        raise EvidenceError("producer_config_fields_invalid")
    activation, freeze = config["activation"], config["freeze"]
    if not isinstance(activation, str) or not activation or not isinstance(freeze, str) or not freeze:
        raise EvidenceError("producer_certified_paths_required")
    return load_activation(root / activation, root / freeze, CANONICAL)


def _state(path: Path, activation: Activation, clock: datetime) -> tuple[list[Envelope], list[Envelope]]:
    previous = store.load(path, activation.identity)
    signals = merge([admission.signal(r, activation, clock) for r in previous["signals"]], "signal_id")
    parents = {r["signal_id"]: r for r in signals}
    outcomes = merge(
        [admission.outcome(r, parents, activation, clock) for r in previous["outcomes"]], "trade_id"
    )
    return signals, outcomes


def _persist(path: Path, activation: Activation, signals: list[Envelope], outcomes: list[Envelope]) -> None:
    store.persist(path, {"schema_version": store.SCHEMA, "identity": activation.identity.mapping(),
                         "signals": signals, "outcomes": outcomes})


def _failure(exc: Exception, *, write: bool) -> ProducerReport:
    if isinstance(exc, EvidenceError):
        reason = str(exc)
    elif isinstance(exc, AtomicWriteError):
        reason = exc.reason
    else:
        reason = "producer_boundary_failed:" + type(exc).__name__
    # Never include a row, sealed payload, configuration or exception values in logs.
    LOGGER.error("qlib_v3_evidence_blocked reason=%s error_type=%s", reason, type(exc).__name__)
    return ProducerReport("blocked", reason, write_requested=write,
                          write_performed=isinstance(exc, AtomicWriteError) and exc.promoted)


def observe_signal_batch(
    *, project_root: Path, signals: Sequence[Mapping[str, Any]],
    decisions: Sequence[DecisionRecordV42], invocation_started_at: datetime,
    runtime_mode: str, config_source: ConfigSource = None,
) -> ProducerReport:
    """Capture only records produced in this invocation, before active publication.

No source file is searched to recover a vanished signal. The actual model hash
must match the certified activation; assigning V3 identity never repairs lineage.
"""
    try:
        activation = _activation(project_root, config_source)
        if activation is None:
            return ProducerReport("disabled", "producer_not_enabled")
        if runtime_mode != "paper":
            raise EvidenceError("natural_paper_runtime_required")
        clock = datetime.now(UTC)
        started = utc(invocation_started_at.isoformat())
        if not activation.boundary <= started <= clock:
            raise EvidenceError("producer_invocation_outside_prospective_window")
        records = {r.event_id: r for r in decisions}
        if len(records) != len(decisions):
            raise EvidenceError("duplicate_decision_event_id")
        incoming: list[Envelope] = []
        for signal in signals:
            envelope = signal.get("decision_ledger")
            if not isinstance(envelope, Mapping):
                raise EvidenceError("ex_ante_sealed_decision_missing")
            record = records.get(str(envelope.get("decision_event_id", "")))
            if record is None or record.final_decision != FinalDecision.ALLOW:
                raise EvidenceError("ex_ante_sealed_allow_required")
            if not started <= record.decision_timestamp <= clock:
                raise EvidenceError("retrospective_signal_capture_forbidden")
            if envelope.get("decision_payload_sha256") != record.payload_sha256:
                raise EvidenceError("published_decision_hash_mismatch")
            for key in ("signal_id", "candidate_id", "correlation_id", "pair", "symbol"):
                if signal.get(key) != getattr(record, key):
                    raise EvidenceError("published_signal_identity_mismatch")
            if signal.get("side") != record.side.value or signal.get("risk_approved") is not True:
                raise EvidenceError("published_signal_risk_or_side_mismatch")
            row = {"epoch_version": "v3", "origin": "natural_paper_runtime",
                   "identity": activation.identity.mapping(), "synthetic": False,
                   "replayed": False, "backfilled": False, "signal_id": record.signal_id,
                   "decision_event_id": record.event_id,
                   "signal_timestamp_utc": record.decision_timestamp.isoformat(),
                   "decision": record.model_dump(mode="json")}
            incoming.append(admission.signal(row, activation, clock))
        if not incoming:
            return ProducerReport("ok", "no_new_natural_signals")
        path = store.location(project_root, activation.identity)
        with store.exclusive(path):
            prior, outcomes = _state(path, activation, clock)
            merged = merge(prior + incoming, "signal_id")
            count = len(merged) - len(prior)
            if count:
                _persist(path, activation, merged, outcomes)
        return ProducerReport("ok", "natural_signal_observed", new_signal_count=count,
                              write_requested=True, write_performed=bool(count))
    except Exception as exc:
        # Evidence fails closed; the existing financial publisher must still run.
        return _failure(exc, write=True)


def _decision_tag(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    values = [part.partition("=")[2] for part in value.split("|")
              if part.partition("=")[0] == "decision_event_id"]
    if not values:
        return None
    if len(values) != 1 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", values[0]):
        raise EvidenceError("ambiguous_or_invalid_trade_decision_tag")
    return values[0]


def _database_time(value: object) -> datetime:
    # Freqtrade's SQLite DateTime columns are UTC, including naive storage.
    # This is the source schema convention, never a timestamp matching heuristic.
    if not isinstance(value, str):
        raise EvidenceError("trade_timestamp_missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return utc(parsed.isoformat())


def _trade_rows(path: Path, ids: Sequence[int]) -> list[dict[str, Any]]:
    safe_path(path)
    if not path.is_file():
        raise EvidenceError("closed_trade_source_missing")
    connection = sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.row_factory = sqlite3.Row
        rows: list[dict[str, Any]] = []
        for offset in range(0, len(ids), 500):
            batch = ids[offset:offset + 500]
            placeholders = ",".join("?" for _ in batch)
            rows.extend(dict(row) for row in connection.execute(
                "SELECT id,pair,is_short,is_open,open_date,close_date,enter_tag FROM trades "  # nosec B608: only '?' markers are interpolated; values are bound in batch.
                f"WHERE id IN ({placeholders}) AND is_open=0 AND close_date IS NOT NULL ORDER BY id",
                batch,
            ))
        return rows
    finally:
        connection.close()


def _closed_envelope(row: Mapping[str, Any], event: Mapping[str, Any], parent: Envelope,
                     activation: Activation) -> Envelope:
    decision = DecisionRecordV42.model_validate(parent["decision"])
    opened, closed = _database_time(row["open_date"]), _database_time(row["close_date"])
    if (event.get("symbol_norm") != decision.symbol or event.get("side") != decision.side.value
            or event.get("is_closed") is not True
            or utc(event.get("open_time_utc")) != opened
            or utc(event.get("close_time_utc")) != closed):
        raise EvidenceError("canonical_feedback_trade_mismatch")
    if row["pair"] != decision.pair or row["is_short"] not in (0, 1):
        raise EvidenceError("closed_trade_pair_or_side_invalid")
    side = "short" if row["is_short"] == 1 else "long"
    if side != decision.side.value:
        raise EvidenceError("closed_trade_side_mismatch")
    token = digest({"decision_event_id": decision.event_id, "trade_id": row["id"]})
    link = seal_trade_link_record({
        "event_id": "v3-trade-" + token, "idempotency_key": "v3-trade-" + token,
        "parent_event_id": decision.event_id, "signal_id": decision.signal_id,
        "candidate_id": decision.candidate_id, "correlation_id": decision.correlation_id,
        "trade_id": row["id"], "pair": row["pair"], "symbol": decision.symbol, "side": side,
        "decision_timestamp": decision.decision_timestamp, "execution_timestamp": opened,
        "decision_payload_sha256": decision.payload_sha256,
        "link_reason": "explicit_decision_event_id_in_enter_tag",
    })
    return {"epoch_version": "v3", "origin": "natural_paper_runtime",
            "identity": activation.identity.mapping(), "synthetic": False,
            "replayed": False, "backfilled": False, "signal_id": decision.signal_id,
            "decision_event_id": decision.event_id, "trade_id": row["id"],
            "trade_link": link.model_dump(mode="json"), "open_time_utc": opened.isoformat(),
            "close_time_utc": closed.isoformat(), "is_closed": True, "net_pnl": event["net_pnl"]}


def observe_feedback_close(
    *, project_root: Path, snapshot_db: Path, events: Sequence[Mapping[str, Any]],
    write: bool, config_source: ConfigSource = None,
) -> ProducerReport:
    """Export real closures only when an ex-ante parent already exists in V3.

The financial values come unchanged from validated AutoLearning events. Old
outcomes cannot acquire a parent here. Re-observation checks content conflicts.
"""
    try:
        activation = _activation(project_root, config_source)
        if activation is None:
            return ProducerReport("disabled", "producer_not_enabled")
        clock = datetime.now(UTC)
        path = store.location(project_root, activation.identity)
        if not path.exists():
            return ProducerReport("ok", "no_ex_ante_v3_parents", skipped_without_parent=len(events))
        with store.exclusive(path) if write else nullcontext():
            signals, prior = _state(path, activation, clock)
            parents = {r["decision_event_id"]: r for r in signals}
            by_signal = {r["signal_id"]: r for r in signals}
            if not parents:
                return ProducerReport("ok", "no_ex_ante_v3_parents", skipped_without_parent=len(events))
            if len(events) > 50000:
                raise EvidenceError("source_row_limit")
            by_trade: dict[int, Mapping[str, Any]] = {}
            for event in events:
                if event.get("validation_status") != "ok":
                    raise EvidenceError("unvalidated_feedback_event")
                key = str(event.get("trade_id", ""))
                if not re.fullmatch(r"[1-9][0-9]*", key):
                    raise EvidenceError("exact_integer_trade_id_required")
                trade_id = int(key)
                fields = ("net_pnl", "symbol_norm", "side", "open_time_utc", "close_time_utc", "is_closed")
                if trade_id in by_trade and any(by_trade[trade_id].get(k) != event.get(k) for k in fields):
                    raise EvidenceError("feedback_trade_identity_conflict")
                by_trade[trade_id] = event
            incoming: list[Envelope] = []
            known = {r["trade_id"] for r in prior}
            observed: set[int] = set()
            for row in _trade_rows(snapshot_db, sorted(by_trade)):
                trade_id = row["id"]
                parent = parents.get(_decision_tag(row["enter_tag"]) or "")
                if parent is None:
                    if trade_id in known:
                        raise EvidenceError("persisted_trade_parent_disappeared")
                    continue
                observed.add(trade_id)
                incoming.append(admission.outcome(
                    _closed_envelope(row, by_trade[trade_id], parent, activation),
                    by_signal, activation, clock,
                ))
            if (known & set(by_trade)) - observed:
                raise EvidenceError("persisted_closed_trade_disappeared")
            merged = merge(prior + incoming, "trade_id")
            count = len(merged) - len(prior)
            if write and count:
                _persist(path, activation, signals, merged)
        return ProducerReport("ok", "natural_closures_observed", new_outcome_count=count,
                              skipped_without_parent=len(by_trade) - len(observed),
                              write_requested=write, write_performed=bool(write and count))
    except Exception as exc:
        return _failure(exc, write=write)
