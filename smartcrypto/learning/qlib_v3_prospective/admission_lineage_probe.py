"""Best-effort, non-authoritative trace of prospective natural admission."""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import store

LOGGER = logging.getLogger(__name__)
SCHEMA_VERSION = "qlib_v3_natural_admission_lineage_probe_v1"
REPORT_NAME = "qlib_v3_natural_admission_lineage_probe_v1.jsonl"
_SAFE_VALUE = re.compile(r"[A-Za-z0-9_./:@-]{1,160}\Z")


def _safe(value: object) -> str | None:
    if not isinstance(value, str) or not _SAFE_VALUE.fullmatch(value):
        return None
    return value


class AdmissionLineageProbe:
    """Keep diagnostic state separate from admission and financial publication."""

    def __init__(self, project_root: Path, signals: Sequence[Mapping[str, Any]]) -> None:
        self.path = project_root / "data/reports" / REPORT_NAME
        self.invocation_id = uuid4().hex
        self.observed_at_utc = datetime.now(UTC).isoformat()
        self.rows: list[dict[str, Any]] = []
        for signal in signals:
            envelope = signal.get("decision_ledger")
            if not isinstance(envelope, Mapping):
                envelope = {}
            row: dict[str, Any] = {
                "schema_version": SCHEMA_VERSION,
                "invocation_id": self.invocation_id,
                "observed_at_utc": self.observed_at_utc,
                "signal_id": _safe(signal.get("signal_id")),
                "decision_event_id": _safe(envelope.get("decision_event_id")),
                "v3_decision_event_id": None,
                "candidate_id": _safe(signal.get("candidate_id")),
                "correlation_id": _safe(signal.get("correlation_id")),
                "pair": _safe(signal.get("pair")),
                "symbol": _safe(signal.get("symbol")),
                "side": _safe(signal.get("side")),
                "stages": [],
                "first_failed_stage": None,
                "shadow_report_status": None,
                "shadow_report_reason": None,
                "candidate_count": None,
                "scored_count": None,
                "selected_count": None,
                "control_count": None,
                "shadow_signal_present": None,
                "shadow_decision_present": None,
                "crosswalk_created": False,
                "persisted_to_v3_store": False,
                "store_guard_status": None,
                "store_guard_reason_code": None,
                "store_guard_wait_seconds": 0.0,
                "store_guard_contention_count": 0,
                "research_only": True,
                "operational_authority": False,
                "sends_orders": False,
                "changes_risk": False,
            }
            self.rows.append(row)
            self.mark(row, "input_received", "ok")

    def matching(self, signal_id: str) -> list[dict[str, Any]]:
        return [row for row in self.rows if row["signal_id"] == signal_id]

    @staticmethod
    def safe_reason(value: object) -> str | None:
        return _safe(value)

    @staticmethod
    def mark(row: dict[str, Any], stage: str, result: str, reason: str | None = None) -> None:
        safe_reason = _safe(reason) if reason is not None else None
        row["stages"].append({"stage": stage, "result": result, "reason": safe_reason})
        if result in {"blocked", "error"} and row["first_failed_stage"] is None:
            row["first_failed_stage"] = stage

    def mark_all(self, stage: str, result: str, reason: str | None = None) -> None:
        for row in self.rows:
            self.mark(row, stage, result, reason)

    def mark_signal(self, signal_id: str, stage: str, result: str, reason: str | None = None) -> None:
        for row in self.matching(signal_id):
            self.mark(row, stage, result, reason)

    def mark_pending(self, signal_ids: list[str], stage: str, result: str, reason: str | None = None) -> None:
        for signal_id in signal_ids:
            self.mark_signal(signal_id, stage, result, reason)

    def guard_acquired(self, receipt: store.GuardReceipt) -> None:
        for row in self.rows:
            if row["store_guard_status"] != "contention_recovered":
                row["store_guard_status"] = receipt.status
            row["store_guard_wait_seconds"] += receipt.wait_seconds
            row["store_guard_contention_count"] += receipt.contention_count

    def guard_timeout(self, error: store.StoreGuardAcquireTimeout) -> None:
        for row in self.rows:
            row["store_guard_status"] = "contention_timeout"
            row["store_guard_reason_code"] = error.reason_code
            row["store_guard_wait_seconds"] += error.wait_seconds
            row["store_guard_contention_count"] += error.contention_count

    def flush(self) -> None:
        """A diagnostic I/O failure cannot change the observer's return value."""
        if not self.rows:
            return
        try:
            payload = b"".join(
                (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
                for row in self.rows
            )
            with store.exclusive(self.path, owner="qlib_v3_admission_lineage_probe",
                                 invocation_id=self.invocation_id):
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                try:
                    view = memoryview(payload)
                    while view:
                        written = os.write(fd, view)
                        if written <= 0:
                            raise OSError("diagnostic_append_incomplete")
                        view = view[written:]
                    os.fsync(fd)
                finally:
                    os.close(fd)
        except Exception as exc:
            LOGGER.warning("qlib_v3_admission_probe_write_failed error_type=%s", type(exc).__name__)
