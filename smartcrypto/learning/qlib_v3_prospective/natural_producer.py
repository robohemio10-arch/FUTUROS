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
from .trade_link_crosswalk import (
    seal_operational_crosswalk,
    validate_operational_crosswalk,
)

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
    shadow_decision_source: str | None = None
    shadow_scored_count: int = 0
    shadow_selected_count: int = 0
    shadow_control_count: int = 0
    shadow_market_source: str | None = None
    operational_model_mismatch_observed: bool = False
    operational_model_mismatch_count: int = 0
    shadow_block_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "qlib_v3_natural_evidence_producer_wiring_v1",
            "status": self.status,
            "reason": self.reason,
            "new_signal_count": self.new_signal_count,
            "new_outcome_count": self.new_outcome_count,
            "skipped_without_parent": self.skipped_without_parent,
            "write_requested": self.write_requested,
            "write_performed": self.write_performed,
            "shadow_decision_source": self.shadow_decision_source,
            "shadow_scored_count": self.shadow_scored_count,
            "shadow_selected_count": self.shadow_selected_count,
            "shadow_control_count": self.shadow_control_count,
            "shadow_market_source": self.shadow_market_source,
            "operational_model_mismatch_observed": (
                self.operational_model_mismatch_observed
            ),
            "operational_model_mismatch_count": (
                self.operational_model_mismatch_count
            ),
            "shadow_block_reason": self.shadow_block_reason,
            "research_only": True,
            "operational_authority": False,
            "paper_behavior_changed": False,
            "sends_orders": False,
            "v2_evidence_imported": False,
            "historical_backfill_allowed": False,
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


def _blocked_report(exc: Exception, *, write: bool) -> ProducerReport:
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


def _validated_input_decision(
    signal: Mapping[str, Any],
    records: Mapping[str, DecisionRecordV42],
) -> DecisionRecordV42:
    """Validate the current sealed operational decision without changing it."""

    envelope = signal.get("decision_ledger")
    if not isinstance(envelope, Mapping):
        raise EvidenceError("ex_ante_sealed_decision_missing")

    record = records.get(
        str(envelope.get("decision_event_id", ""))
    )
    if (
        record is None
        or record.final_decision != FinalDecision.ALLOW
    ):
        raise EvidenceError("ex_ante_sealed_allow_required")

    if (
        envelope.get("decision_payload_sha256")
        != record.payload_sha256
    ):
        raise EvidenceError("published_decision_hash_mismatch")

    for key in (
        "signal_id",
        "candidate_id",
        "correlation_id",
        "pair",
        "symbol",
    ):
        if signal.get(key) != getattr(record, key):
            raise EvidenceError(
                "published_signal_identity_mismatch"
            )

    if (
        signal.get("side") != record.side.value
        or signal.get("risk_approved") is not True
    ):
        raise EvidenceError(
            "published_signal_risk_or_side_mismatch"
        )

    return record


def _validated_persisted_signal(
    signal: Mapping[str, Any],
    persisted_row: Mapping[str, Any],
) -> DecisionRecordV42:
    """Validate stable occurrence identity against an admitted V3 parent."""

    persisted = DecisionRecordV42.model_validate(
        persisted_row.get("decision")
    )

    exact = (
        signal.get("signal_id") == persisted.signal_id
        and signal.get("candidate_id") == persisted.candidate_id
        and signal.get("correlation_id") == persisted.correlation_id
        and signal.get("pair") == persisted.pair
        and signal.get("symbol") == persisted.symbol
        and str(signal.get("side") or "").lower()
        == persisted.side.value
        and signal.get("risk_approved") is True
    )
    if not exact:
        raise EvidenceError(
            "persisted_signal_identity_conflict"
        )
    return persisted


def _existing_signal_is_noop(
    *,
    signal: Mapping[str, Any],
    current_record: DecisionRecordV42,
    persisted_row: Mapping[str, Any],
    activation: Activation,
) -> bool:
    """Validate a repeated occurrence without changing its admitted lineage.

    Signals admitted before the operational-crosswalk contract remain valid and
    preserve the historical store-first behavior. Once a signal contains a
    sealed operational crosswalk, every re-observation of that same signal
    occurrence must present the exact same operational decision event and
    payload hash. This also closes the race where concurrent observers see the
    same signal_id with divergent operational decisions.
    """

    persisted = _validated_persisted_signal(
        signal,
        persisted_row,
    )

    if (
        current_record.model_hash
        == activation.identity.model_artifact_sha256
        and current_record.payload_sha256
        != persisted.payload_sha256
    ):
        raise EvidenceError(
            "causal_identity_content_conflict"
        )

    crosswalk = validate_operational_crosswalk(
        persisted_row.get("operational_crosswalk"),
        v3_decision=persisted,
    )
    if crosswalk is not None and (
        current_record.event_id
        != crosswalk["operational_decision_event_id"]
        or current_record.payload_sha256
        != crosswalk["operational_decision_payload_sha256"]
    ):
        raise EvidenceError(
            "operational_crosswalk_content_conflict"
        )

    return True


def observe_signal_batch(
    *,
    project_root: Path,
    signals: Sequence[Mapping[str, Any]],
    decisions: Sequence[DecisionRecordV42],
    invocation_started_at: datetime,
    runtime_mode: str,
    config_source: ConfigSource = None,
) -> ProducerReport:
    """Capture only new ex-ante V3 signal occurrences before Paper publication.

    Store-first idempotency prevents a repeated operational ``signal_id`` from
    being rescored. A second locked recheck closes the race between scoring and
    persistence. New occurrences persist an immutable exact crosswalk from the
    validated operational decision to the V3 shadow decision before Paper
    publication. Legacy Paper decisions remain non-authoritative and the
    operational signal object is never changed.
    """

    try:
        activation = _activation(
            project_root,
            config_source,
        )
        if activation is None:
            return ProducerReport(
                "disabled",
                "producer_not_enabled",
            )
        if runtime_mode != "paper":
            raise EvidenceError(
                "natural_paper_runtime_required"
            )

        clock = datetime.now(UTC)
        started = utc(
            invocation_started_at.isoformat()
        )
        if not (
            activation.boundary
            <= started
            <= clock
        ):
            raise EvidenceError(
                "producer_invocation_outside_prospective_window"
            )

        records = {
            record.event_id: record
            for record in decisions
        }
        if len(records) != len(decisions):
            raise EvidenceError(
                "duplicate_decision_event_id"
            )

        validated: list[
            tuple[Mapping[str, Any], DecisionRecordV42]
        ] = [
            (
                signal,
                _validated_input_decision(
                    signal,
                    records,
                ),
            )
            for signal in signals
        ]

        signal_ids = [
            record.signal_id
            for _, record in validated
        ]
        if len(set(signal_ids)) != len(signal_ids):
            raise EvidenceError(
                "duplicate_signal_id"
            )

        path = store.location(
            project_root,
            activation.identity,
        )
        prior_by_signal: dict[str, Envelope] = {}

        if path.exists():
            with store.exclusive(path):
                prior_signals, _ = _state(
                    path,
                    activation,
                    clock,
                )
            prior_by_signal = {
                str(row["signal_id"]): row
                for row in prior_signals
            }

        pending: list[
            tuple[Mapping[str, Any], DecisionRecordV42]
        ] = []
        for signal, record in validated:
            persisted = prior_by_signal.get(
                record.signal_id
            )
            if persisted is None:
                pending.append(
                    (signal, record)
                )
                continue

            _existing_signal_is_noop(
                signal=signal,
                current_record=record,
                persisted_row=persisted,
                activation=activation,
            )

        if not pending:
            return ProducerReport(
                "ok",
                "no_new_natural_signals",
                write_requested=True,
                write_performed=False,
                shadow_decision_source=(
                    "persisted_v3_signal"
                ),
                shadow_market_source=(
                    "persisted_v3_signal"
                ),
            )

        pending_signals = tuple(
            signal
            for signal, _ in pending
        )
        pending_decisions = tuple(
            record
            for _, record in pending
        )
        pending_by_signal = {
            record.signal_id: (
                signal,
                record,
            )
            for signal, record in pending
        }

        operational_model_mismatch_count = sum(
            record.model_hash
            != activation.identity.model_artifact_sha256
            for record in pending_decisions
        )
        operational_model_mismatch_observed = bool(
            operational_model_mismatch_count
        )

        from .economic_shadow_decision_producer import (
            resolve_shadow_decision_batch,
        )

        shadow = resolve_shadow_decision_batch(
            project_root=project_root,
            signals=pending_signals,
            existing_decisions=pending_decisions,
            decision_timestamp_utc=clock,
            activation=activation,
            config_source=config_source,
        )

        if shadow.report.status != "ok":
            shadow_block_reason = str(
                shadow.report.reason
            )
            LOGGER.warning(
                "qlib_v3_shadow_blocked "
                "reason=%s operational_model_mismatch_count=%s",
                shadow_block_reason,
                operational_model_mismatch_count,
            )
            return ProducerReport(
                "blocked",
                shadow_block_reason,
                write_requested=True,
                shadow_decision_source=(
                    shadow.report.decision_source
                ),
                shadow_scored_count=(
                    shadow.report.scored_count
                ),
                shadow_selected_count=(
                    shadow.report.selected_count
                ),
                shadow_control_count=(
                    shadow.report.control_count
                ),
                shadow_market_source=(
                    shadow.report.market_source
                ),
                operational_model_mismatch_observed=(
                    operational_model_mismatch_observed
                ),
                operational_model_mismatch_count=(
                    operational_model_mismatch_count
                ),
                shadow_block_reason=shadow_block_reason,
            )

        resolved_signals = shadow.signals
        resolved_decisions = shadow.decisions
        resolved_records = {
            record.event_id: record
            for record in resolved_decisions
        }
        if len(resolved_records) != len(
            resolved_decisions
        ):
            raise EvidenceError(
                "duplicate_decision_event_id"
            )

        incoming: list[Envelope] = []
        for signal in resolved_signals:
            envelope = signal.get(
                "decision_ledger"
            )
            if not isinstance(
                envelope,
                Mapping,
            ):
                raise EvidenceError(
                    "ex_ante_sealed_decision_missing"
                )

            record = resolved_records.get(
                str(
                    envelope.get(
                        "decision_event_id",
                        "",
                    )
                )
            )
            if (
                record is None
                or record.final_decision
                != FinalDecision.ALLOW
            ):
                raise EvidenceError(
                    "ex_ante_sealed_allow_required"
                )

            if not (
                started
                <= record.decision_timestamp
                <= clock
            ):
                raise EvidenceError(
                    "retrospective_signal_capture_forbidden"
                )

            if (
                envelope.get(
                    "decision_payload_sha256"
                )
                != record.payload_sha256
            ):
                raise EvidenceError(
                    "published_decision_hash_mismatch"
                )

            for key in (
                "signal_id",
                "candidate_id",
                "correlation_id",
                "pair",
                "symbol",
            ):
                if signal.get(key) != getattr(
                    record,
                    key,
                ):
                    raise EvidenceError(
                        "published_signal_identity_mismatch"
                    )

            if (
                signal.get("side")
                != record.side.value
                or signal.get("risk_approved")
                is not True
            ):
                raise EvidenceError(
                    "published_signal_risk_or_side_mismatch"
                )

            original = pending_by_signal.get(
                record.signal_id
            )
            if original is None:
                raise EvidenceError(
                    "pending_signal_identity_missing"
                )
            _, operational_record = original

            crosswalk = seal_operational_crosswalk(
                operational_record=operational_record,
                v3_decision=record,
            )

            row = {
                "epoch_version": "v3",
                "origin": "natural_paper_runtime",
                "identity": (
                    activation.identity.mapping()
                ),
                "synthetic": False,
                "replayed": False,
                "backfilled": False,
                "signal_id": record.signal_id,
                "decision_event_id": record.event_id,
                "signal_timestamp_utc": (
                    record.decision_timestamp.isoformat()
                ),
                "decision": record.model_dump(
                    mode="json"
                ),
                "operational_crosswalk": crosswalk,
            }
            incoming.append(
                admission.signal(
                    row,
                    activation,
                    clock,
                )
            )

        with store.exclusive(path):
            prior, outcomes = _state(
                path,
                activation,
                clock,
            )
            current_by_signal = {
                str(row["signal_id"]): row
                for row in prior
            }

            truly_new: list[Envelope] = []
            for row in incoming:
                signal_id = str(
                    row["signal_id"]
                )
                persisted = current_by_signal.get(
                    signal_id
                )
                if persisted is None:
                    truly_new.append(row)
                    continue

                current = pending_by_signal.get(
                    signal_id
                )
                if current is None:
                    raise EvidenceError(
                        "pending_signal_identity_missing"
                    )
                signal, input_record = current
                _existing_signal_is_noop(
                    signal=signal,
                    current_record=input_record,
                    persisted_row=persisted,
                    activation=activation,
                )

            merged = merge(
                prior + truly_new,
                "signal_id",
            )
            count = len(merged) - len(prior)
            if count:
                _persist(
                    path,
                    activation,
                    merged,
                    outcomes,
                )

        return ProducerReport(
            "ok",
            (
                "natural_signal_observed"
                if count
                else "no_new_natural_signals"
            ),
            new_signal_count=count,
            write_requested=True,
            write_performed=bool(count),
            shadow_decision_source=(
                shadow.report.decision_source
            ),
            shadow_scored_count=(
                shadow.report.scored_count
            ),
            shadow_selected_count=(
                shadow.report.selected_count
            ),
            shadow_control_count=(
                shadow.report.control_count
            ),
            shadow_market_source=(
                shadow.report.market_source
            ),
            operational_model_mismatch_observed=(
                operational_model_mismatch_observed
            ),
            operational_model_mismatch_count=(
                operational_model_mismatch_count
            ),
            shadow_block_reason=None,
        )

    except Exception as exc:
        return _blocked_report(
            exc,
            write=True,
        )

def _operational_parent_index(
    signals: Sequence[Envelope],
) -> dict[str, Envelope]:
    """Index only explicitly sealed operational->V3 crosswalks."""

    index: dict[str, Envelope] = {}
    for parent in signals:
        decision = DecisionRecordV42.model_validate(
            parent["decision"]
        )
        crosswalk = validate_operational_crosswalk(
            parent.get("operational_crosswalk"),
            v3_decision=decision,
        )
        if crosswalk is None:
            continue

        event_id = crosswalk[
            "operational_decision_event_id"
        ]
        previous = index.get(event_id)
        if (
            previous is not None
            and previous["signal_id"] != parent["signal_id"]
        ):
            raise EvidenceError(
                "operational_crosswalk_identity_collision"
            )
        index[event_id] = parent
    return index


def _resolve_trade_parent(
    decision_event_id: str | None,
    *,
    v3_parents: Mapping[str, Envelope],
    operational_parents: Mapping[str, Envelope],
) -> tuple[Envelope | None, str | None]:
    """Resolve a trade parent by exact identifier only."""

    if decision_event_id is None:
        return None, None

    direct = v3_parents.get(decision_event_id)
    crossed = operational_parents.get(
        decision_event_id
    )

    if (
        direct is not None
        and crossed is not None
        and direct["signal_id"] != crossed["signal_id"]
    ):
        raise EvidenceError(
            "trade_decision_tag_parent_collision"
        )

    if direct is not None:
        return (
            direct,
            "explicit_decision_event_id_in_enter_tag",
        )
    if crossed is not None:
        return (
            crossed,
            "explicit_operational_decision_event_id_crosswalk",
        )
    return None, None

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


def _closed_envelope(
    row: Mapping[str, Any],
    event: Mapping[str, Any],
    parent: Envelope,
    activation: Activation,
    *,
    link_reason: str,
) -> Envelope:
    if link_reason not in {
        "explicit_decision_event_id_in_enter_tag",
        "explicit_operational_decision_event_id_crosswalk",
    }:
        raise EvidenceError("trade_link_reason_invalid")

    decision = DecisionRecordV42.model_validate(parent["decision"])
    opened = _database_time(row["open_date"])
    closed = _database_time(row["close_date"])

    if (
        event.get("symbol_norm") != decision.symbol
        or event.get("side") != decision.side.value
        or event.get("is_closed") is not True
        or utc(event.get("open_time_utc")) != opened
        or utc(event.get("close_time_utc")) != closed
    ):
        raise EvidenceError("canonical_feedback_trade_mismatch")

    if row["pair"] != decision.pair or row["is_short"] not in (0, 1):
        raise EvidenceError("closed_trade_pair_or_side_invalid")

    side = "short" if row["is_short"] == 1 else "long"
    if side != decision.side.value:
        raise EvidenceError("closed_trade_side_mismatch")

    token = digest(
        {
            "decision_event_id": decision.event_id,
            "trade_id": row["id"],
        }
    )
    link = seal_trade_link_record(
        {
            "event_id": "v3-trade-" + token,
            "idempotency_key": "v3-trade-" + token,
            "parent_event_id": decision.event_id,
            "signal_id": decision.signal_id,
            "candidate_id": decision.candidate_id,
            "correlation_id": decision.correlation_id,
            "trade_id": row["id"],
            "pair": row["pair"],
            "symbol": decision.symbol,
            "side": side,
            "decision_timestamp": decision.decision_timestamp,
            "execution_timestamp": opened,
            "decision_payload_sha256": decision.payload_sha256,
            "link_reason": link_reason,
        }
    )
    return {
        "epoch_version": "v3",
        "origin": "natural_paper_runtime",
        "identity": activation.identity.mapping(),
        "synthetic": False,
        "replayed": False,
        "backfilled": False,
        "signal_id": decision.signal_id,
        "decision_event_id": decision.event_id,
        "trade_id": row["id"],
        "trade_link": link.model_dump(mode="json"),
        "open_time_utc": opened.isoformat(),
        "close_time_utc": closed.isoformat(),
        "is_closed": True,
        "net_pnl": event["net_pnl"],
    }



def observe_feedback_close(
    *,
    project_root: Path,
    snapshot_db: Path,
    events: Sequence[Mapping[str, Any]],
    write: bool,
    config_source: ConfigSource = None,
) -> ProducerReport:
    """Export real closures only when an exact ex-ante V3 parent is provable.

    Direct V3 decision ids remain supported for backward compatibility.
    Operational decision ids are accepted only when the V3 parent already
    contains an immutable ex-ante crosswalk. Signals admitted before the
    crosswalk contract are never retrofitted or matched by time, pair, order,
    proximity, fuzzy identity, or nearest-neighbour inference.
    """

    try:
        activation = _activation(
            project_root,
            config_source,
        )
        if activation is None:
            return ProducerReport(
                "disabled",
                "producer_not_enabled",
            )

        clock = datetime.now(UTC)
        path = store.location(
            project_root,
            activation.identity,
        )
        if not path.exists():
            return ProducerReport(
                "ok",
                "no_ex_ante_v3_parents",
                skipped_without_parent=len(events),
            )

        with (
            store.exclusive(path)
            if write
            else nullcontext()
        ):
            signals, prior = _state(
                path,
                activation,
                clock,
            )
            v3_parents = {
                row["decision_event_id"]: row
                for row in signals
            }
            operational_parents = (
                _operational_parent_index(signals)
            )
            by_signal = {
                row["signal_id"]: row
                for row in signals
            }

            if not v3_parents:
                return ProducerReport(
                    "ok",
                    "no_ex_ante_v3_parents",
                    skipped_without_parent=len(events),
                )

            if len(events) > 50000:
                raise EvidenceError("source_row_limit")

            by_trade: dict[
                int,
                Mapping[str, Any],
            ] = {}
            for event in events:
                if event.get("validation_status") != "ok":
                    raise EvidenceError(
                        "unvalidated_feedback_event"
                    )

                key = str(event.get("trade_id", ""))
                if not re.fullmatch(
                    r"[1-9][0-9]*",
                    key,
                ):
                    raise EvidenceError(
                        "exact_integer_trade_id_required"
                    )

                trade_id = int(key)
                fields = (
                    "net_pnl",
                    "symbol_norm",
                    "side",
                    "open_time_utc",
                    "close_time_utc",
                    "is_closed",
                )
                if (
                    trade_id in by_trade
                    and any(
                        by_trade[trade_id].get(field)
                        != event.get(field)
                        for field in fields
                    )
                ):
                    raise EvidenceError(
                        "feedback_trade_identity_conflict"
                    )
                by_trade[trade_id] = event

            incoming: list[Envelope] = []
            known = {
                row["trade_id"]
                for row in prior
            }
            observed: set[int] = set()

            for row in _trade_rows(
                snapshot_db,
                sorted(by_trade),
            ):
                trade_id = row["id"]
                decision_event_id = _decision_tag(
                    row["enter_tag"]
                )
                parent, link_reason = (
                    _resolve_trade_parent(
                        decision_event_id,
                        v3_parents=v3_parents,
                        operational_parents=(
                            operational_parents
                        ),
                    )
                )

                if parent is None or link_reason is None:
                    if trade_id in known:
                        raise EvidenceError(
                            "persisted_trade_parent_disappeared"
                        )
                    continue

                observed.add(trade_id)
                incoming.append(
                    admission.outcome(
                        _closed_envelope(
                            row,
                            by_trade[trade_id],
                            parent,
                            activation,
                            link_reason=link_reason,
                        ),
                        by_signal,
                        activation,
                        clock,
                    )
                )

            if (
                (known & set(by_trade))
                - observed
            ):
                raise EvidenceError(
                    "persisted_closed_trade_disappeared"
                )

            merged = merge(
                prior + incoming,
                "trade_id",
            )
            count = len(merged) - len(prior)
            if write and count:
                _persist(
                    path,
                    activation,
                    signals,
                    merged,
                )

        return ProducerReport(
            "ok",
            "natural_closures_observed",
            new_outcome_count=count,
            skipped_without_parent=(
                len(by_trade) - len(observed)
            ),
            write_requested=write,
            write_performed=bool(
                write and count
            ),
        )
    except Exception as exc:
        return _blocked_report(
            exc,
            write=write,
        )
