"""One explicit read-only observation cycle; no daemon, training or runtime wiring."""

from __future__ import annotations

import hashlib
import io
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from smartcrypto.runtime.integrity_traceability_v2 import AtomicWriteError

from . import admission, store
from .activation import MAX_JSON_BYTES, load_activation, parse_json, read_bytes, safe_path
from .contracts import CANONICAL, SAFETY, EvidenceError, Identity, digest


def source(path: Path | None, kind: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if path is None or not path.exists():
        return [], {"status": "missing", "rows": 0, "sha256": None}
    safe_path(path)
    if path.suffix == ".parquet":
        import pyarrow.parquet as pq

        raw = read_bytes(path)
        parquet = pq.ParquetFile(io.BytesIO(raw))
        try:
            if parquet.metadata.num_rows > 50000:
                raise EvidenceError("source_row_limit")
            if sum(parquet.metadata.row_group(i).total_byte_size
                   for i in range(parquet.num_row_groups)) > MAX_JSON_BYTES * 4:
                raise EvidenceError("source_decoded_size_limit")
            rows = []
            for batch in parquet.iter_batches(batch_size=1000):
                rows.extend(batch.to_pylist())
        finally:
            parquet.close()
        return rows, {"status": "available", "rows": len(rows),
                      "sha256": hashlib.sha256(raw).hexdigest()}
    if path.suffix not in {".json", ".jsonl"}:
        raise EvidenceError("source_extension_unsupported")
    raw = read_bytes(path)
    payload_rows: Any
    if path.suffix == ".jsonl":
        payload_rows = [parse_json(line) for line in raw.splitlines() if line.strip()]
    else:
        payload = parse_json(raw)
        payload_rows = payload.get(kind) if isinstance(payload, dict) else payload
    if not isinstance(payload_rows, list) or any(not isinstance(r, dict) for r in payload_rows):
        raise EvidenceError(f"{kind}_source_schema_invalid")
    if len(payload_rows) > 50000:
        raise EvidenceError("source_row_limit")
    return payload_rows, {"status": "available", "rows": len(payload_rows),
                  "sha256": hashlib.sha256(raw).hexdigest()}


def merge(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    decisions: dict[str, str] = {}
    for row in rows:
        identity = str(row[key])
        prior = result.get(identity)
        if prior is not None and digest(prior) != digest(row):
            raise EvidenceError("causal_identity_content_conflict")
        if key == "signal_id":
            decision = row["decision_event_id"]
            if decision in decisions and decisions[decision] != identity:
                raise EvidenceError("decision_identity_collision")
            decisions[decision] = identity
        result[identity] = row
    return [result[k] for k in sorted(result)]


def run_cycle(*, project_root: Path, activation_path: Path, freeze_path: Path,
              signals_path: Path | None = None, outcomes_path: Path | None = None,
              write: bool = False, expected: Identity = CANONICAL,
              now: datetime | None = None) -> dict[str, Any]:
    report: dict[str, Any] = {"schema_version": "qlib_v3_prospective_evidence_orchestrator_v1",
        "status": "blocked", "decision": "AWAITING_NATURAL_V3_EVIDENCE",
        "identity": expected.mapping(), "eligible_signal_count": 0, "eligible_outcome_count": 0,
        "signal_count": 0, "outcome_count": 0, "prospective_counter_base_signal": 0,
        "prospective_counter_base_outcome": 0, "write_requested": write, "write_performed": False,
        "new_signal_count": 0, "new_outcome_count": 0, "rejections": [], "sources": {},
        "natural_source_status": "not_inspected", "safety_flags": dict(SAFETY), **SAFETY}
    try:
        activation = load_activation(activation_path, freeze_path, expected)
        clock = now or datetime.now(UTC)
        if clock.tzinfo is None or clock.utcoffset() is None:
            raise EvidenceError("clock_requires_timezone")
        clock = clock.astimezone(UTC)
        incoming, signal_source = source(signals_path, "signals")
        closed, outcome_source = source(outcomes_path, "outcomes")
        report["sources"] = {"signals": signal_source, "outcomes": outcome_source}
        report["activation_file_sha256"] = activation.activation_file_sha256
        report["freeze_file_sha256"] = activation.freeze_file_sha256
        path = store.location(project_root, expected)
        # The outer guard covers read/merge/write, not just the final replacement.
        with store.exclusive(path) if write else nullcontext():
            previous = store.load(path, expected)
            signals = [admission.signal(r, activation, clock) for r in previous["signals"]]
            signals = merge(signals, "signal_id")
            old_signal_count = len(signals)
            accepted: list[dict[str, Any]] = []
            for index, row in enumerate(incoming):
                try:
                    accepted.append(admission.signal(row, activation, clock))
                except (EvidenceError, ValidationError) as exc:
                    reason = str(exc) if isinstance(exc, EvidenceError) else "invalid_sealed_decision"
                    report["rejections"].append({"source": "signals", "row": index, "reason": reason})
            signals = merge(signals + accepted, "signal_id")
            by_signal = {r["signal_id"]: r for r in signals}
            outcomes = [admission.outcome(r, by_signal, activation, clock) for r in previous["outcomes"]]
            outcomes = merge(outcomes, "trade_id")
            old_outcome_count = len(outcomes)
            accepted = []
            for index, row in enumerate(closed):
                try:
                    accepted.append(admission.outcome(row, by_signal, activation, clock))
                except (EvidenceError, ValidationError) as exc:
                    reason = str(exc) if isinstance(exc, EvidenceError) else "invalid_sealed_trade_link"
                    report["rejections"].append({"source": "outcomes", "row": index, "reason": reason})
            outcomes = merge(outcomes + accepted, "trade_id")
            report.update(eligible_signal_count=len(signals), eligible_outcome_count=len(outcomes),
                          signal_count=len(signals), outcome_count=len(outcomes),
                          new_signal_count=len(signals)-old_signal_count,
                          new_outcome_count=len(outcomes)-old_outcome_count)
            if write and (report["new_signal_count"] or report["new_outcome_count"]):
                store.persist(path, {"schema_version": store.SCHEMA, "identity": expected.mapping(),
                                     "signals": signals, "outcomes": outcomes})
                report["write_performed"] = True
        report["natural_source_status"] = (
            "eligible_v3_evidence" if signals else "no_eligible_v3_evidence"
            if signal_source["status"] == "available" else "signal_source_missing")
        report["status"] = "ok" if signals else "waiting"
        report["decision"] = "OBSERVING_NATURAL_V3_EVIDENCE" if signals else "AWAITING_NATURAL_V3_EVIDENCE"
        report["reason"] = report["natural_source_status"]
    except AtomicWriteError as exc:
        report.update(status="blocked", reason=exc.reason, write_performed=exc.promoted)
    except (EvidenceError, ValidationError, OSError, TypeError, KeyError, ValueError) as exc:
        report.update(status="blocked", reason=str(exc) if isinstance(exc, EvidenceError)
                      else f"invalid_or_unreadable_source:{type(exc).__name__}")
    return report
