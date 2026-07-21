"""Injected projection sinks; runtime persistence is disabled in P0.4C."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from smartcrypto.execution.decision_ledger_v4_2 import DecisionLedgerWriter
from smartcrypto.execution.decision_ledger_runtime_profile_v1 import RuntimeProjectionRecordV1


class ProjectionWriteDisabledError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProjectionReceipt:
    event_id: str
    idempotency_key: str
    payload_sha256: str
    duplicate: bool
    sink_type: str


class ProjectionSink(Protocol):
    def append(self, projection: RuntimeProjectionRecordV1) -> ProjectionReceipt: ...


class DisabledProjectionSink:
    sink_type = "disabled"

    def append(self, projection: RuntimeProjectionRecordV1) -> ProjectionReceipt:
        del projection
        raise ProjectionWriteDisabledError("projection_writer_disabled_in_p0_4c")

    def health(self) -> dict[str, object]:
        return {
            "status": "disabled",
            "writer_invoked": False,
            "runtime_integration": False,
        }


class InMemoryProjectionSink:
    """Deterministic test sink with idempotent duplicate suppression."""

    sink_type = "memory_test_only"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, RuntimeProjectionRecordV1] = {}

    def append(self, projection: RuntimeProjectionRecordV1) -> ProjectionReceipt:
        target = projection.target_payload
        with self._lock:
            duplicate = target.idempotency_key in self._records
            if not duplicate:
                self._records[target.idempotency_key] = projection
        return ProjectionReceipt(
            event_id=target.event_id,
            idempotency_key=target.idempotency_key,
            payload_sha256=target.payload_sha256,
            duplicate=duplicate,
            sink_type=self.sink_type,
        )

    def records(self) -> tuple[RuntimeProjectionRecordV1, ...]:
        with self._lock:
            return tuple(self._records[key] for key in sorted(self._records))

    def health(self) -> dict[str, object]:
        with self._lock:
            count = len(self._records)
        return {
            "status": "healthy",
            "record_count": count,
            "writer_invoked": count > 0,
            "runtime_integration": False,
        }


class SandboxFileProjectionSink:
    """Explicit test-only adapter over P0.3B writer under a disposable root."""

    sink_type = "sandbox_file_test_only"

    def __init__(
        self,
        *,
        allowed_root: Path,
        ledger_path: Path,
        health_path: Path,
        lock_timeout_seconds: float = 0.2,
        fsync_enabled: bool = True,
    ) -> None:
        self.allowed_root = allowed_root.resolve(strict=False)
        self.writer = DecisionLedgerWriter(
            ledger_path=ledger_path,
            health_path=health_path,
            allowed_root=self.allowed_root,
            design_only=True,
            lock_timeout_seconds=lock_timeout_seconds,
            fsync_enabled=fsync_enabled,
        )
        self._index_lock = threading.Lock()
        self._idempotency_index: dict[str, str] = self._load_index()

    def append(self, projection: RuntimeProjectionRecordV1) -> ProjectionReceipt:
        target = projection.target_payload
        with self._index_lock:
            previous_hash = self._idempotency_index.get(target.idempotency_key)
            if previous_hash is not None:
                if previous_hash != target.payload_sha256:
                    raise ValueError("idempotency_key_payload_conflict")
                return ProjectionReceipt(
                    event_id=target.event_id,
                    idempotency_key=target.idempotency_key,
                    payload_sha256=target.payload_sha256,
                    duplicate=True,
                    sink_type=self.sink_type,
                )
            receipt = self.writer.append(target)
            self._idempotency_index[target.idempotency_key] = target.payload_sha256
            return ProjectionReceipt(
                event_id=receipt.event_id,
                idempotency_key=target.idempotency_key,
                payload_sha256=receipt.payload_sha256,
                duplicate=False,
                sink_type=self.sink_type,
            )

    def health(self) -> dict[str, object]:
        state = self.writer.read_health()
        return state.model_dump(mode="json")

    def _load_index(self) -> dict[str, str]:
        ledger_path = self.writer.ledger_path
        if not ledger_path.is_file():
            return {}
        index: dict[str, str] = {}
        for line_number, line in enumerate(
            ledger_path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not line.strip():
                continue
            payload = json.loads(line)
            key = str(payload["idempotency_key"])
            payload_hash = str(payload["payload_sha256"])
            previous = index.get(key)
            if previous is not None and previous != payload_hash:
                raise ValueError(f"ledger_idempotency_conflict_at_line:{line_number}")
            index[key] = payload_hash
        return index


def build_default_projection_sink() -> DisabledProjectionSink:
    return DisabledProjectionSink()
