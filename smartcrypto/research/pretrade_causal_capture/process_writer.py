"""Explicit synthetic harness consumer; bounded IPC, no worker on import/default."""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import time
from dataclasses import dataclass
from itertools import islice
from multiprocessing.process import BaseProcess
from multiprocessing.queues import Queue as ProcessQueue
from multiprocessing.synchronize import Event
from pathlib import Path
from queue import Empty, Full
from typing import TYPE_CHECKING, Literal

from smartcrypto.ops.execution_decision_l1.archive import validate_external_root
from smartcrypto.runtime.integrity_traceability_v2.atomic_writer import (
    AtomicWriteError,
    AtomicWritePolicy,
    _InterProcessFileLock,
    atomic_write_json,
)

from .archive import EvidenceArchive
from .contracts import Evidence, digest, parse_record

if TYPE_CHECKING:
    from .source_model_proof import ModelCallProof


@dataclass(frozen=True)
class WriterResult:
    status: Literal["disabled", "passed", "blocked"]
    reason: str
    offered: int
    persisted: int
    dropped: int
    child_pid: int | None
    parent_pid: int
    shutdown_complete: bool
    queue_capacity: int
    model_calls_persisted: int = 0
    max_message_bytes: int = 65536
    max_archive_bytes: int = 32 * 1024**2
    max_binding_archive_bytes: int = 4 * 1024**2
    hard_rss_limit_enforced: bool = False
    operational_authority: bool = False


def _persist_model_call(archive: EvidenceArchive, record: ModelCallProof) -> None:
    """Create-once auxiliary proof using institutional atomic I/O and locking."""
    root = validate_external_root(archive.root, archive.forbidden_roots)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"model-call-{digest([record.epoch_id, record.call_id])}.json"
    validate_external_root(path, archive.forbidden_roots)
    guard = _InterProcessFileLock(root / ".model-call.guard", timeout_seconds=0.05)
    guard.acquire()
    try:
        if path.exists():
            with path.open("rb") as handle:
                previous = handle.read(65537)
            if len(previous) > 65536 or json.loads(previous) != record.model_dump(mode="json"):
                raise ValueError("model_call_identity_conflict")
            return
        paths = list(islice(root.glob("model-call-*.json"), 65))
        payload = record.model_dump(mode="json")
        size = len(json.dumps(payload, indent=2, sort_keys=True).encode()) + 1
        if (
            len(paths) >= 64
            or size > 65536
            or sum(p.stat().st_size for p in paths) + size > 4 * 1024**2
        ):
            raise ValueError("model_call_archive_budget_exceeded")
        atomic_write_json(
            path,
            payload,
            policy=AtomicWritePolicy.restricted([root], lock_timeout_seconds=0.05),
            allow_nan=False,
        )
    finally:
        guard.release()


def _writer_main(
    queue: ProcessQueue[bytes | None],
    result_queue: ProcessQueue[tuple[int, int, str]],
    ready: Event,
    root: str,
    forbidden: tuple[str, ...],
    max_records: int,
) -> None:
    persisted = 0
    model_calls = 0
    reason = "completed"
    ready.set()
    archive = EvidenceArchive(
        Path(root),
        authorized=True,
        forbidden_roots=tuple(Path(p) for p in forbidden),
        max_records=max_records,
    )
    try:
        while True:
            raw = queue.get(timeout=15)
            if raw is None:
                break
            if not isinstance(raw, bytes) or len(raw) > 65536:
                raise ValueError("invalid_ipc_record")
            body = json.loads(raw)
            if (
                isinstance(body, dict)
                and body.get("schema_version") == "paper_pit_model_call_harness_v1"
            ):
                from .source_model_proof import ModelCallProof

                proof = ModelCallProof.model_validate(body)
                _persist_model_call(archive, proof)
                model_calls += 1
            else:
                record = parse_record(body)
                if not record.synthetic:
                    raise ValueError("synthetic_harness_only")
                archive.persist(record)
            persisted += 1
            if persisted > max_records:
                raise ValueError("writer_record_budget_exceeded")
    except (OSError, ValueError, Empty, AtomicWriteError):
        reason = "archive_persistence_failed"
    result_queue.put((persisted, model_calls, reason), timeout=1)


class IsolatedWriter:
    def __init__(
        self,
        *,
        enabled: bool = False,
        root: Path,
        forbidden_roots: tuple[Path, ...],
        queue_capacity: int = 8,
        max_records: int = 64,
    ) -> None:
        if not 1 <= queue_capacity <= 32 or not 1 <= max_records <= 256:
            raise ValueError("writer_bounds_invalid")
        self.enabled, self.root, self.forbidden = enabled, root, forbidden_roots
        self.queue_capacity, self.max_records = queue_capacity, max_records
        self._process: BaseProcess | None = None
        self._queue: ProcessQueue[bytes | None] | None = None
        self._result_queue: ProcessQueue[tuple[int, int, str]] | None = None
        self._offered = self._dropped = 0
        self._fault: str | None = None
        self._finished = False

    def start(self) -> None:
        if not self.enabled:
            return
        if self._process is not None or self._finished or not self.forbidden:
            raise ValueError("writer_start_not_authorized")
        root = validate_external_root(self.root, self.forbidden)
        context = mp.get_context("spawn")
        self._queue = context.Queue(maxsize=self.queue_capacity)
        self._result_queue = context.Queue(maxsize=1)
        ready = context.Event()
        self._process = context.Process(
            target=_writer_main,
            args=(
                self._queue,
                self._result_queue,
                ready,
                str(root),
                tuple(str(p) for p in self.forbidden),
                self.max_records,
            ),
            name="synthetic-pit-evidence-writer",
            daemon=True,
        )
        self._process.start()
        if not ready.wait(timeout=15):
            self._fault = "writer_start_timeout"
            self.finish(timeout_seconds=0.1)
            raise ValueError("writer_start_timeout")

    def submit(self, record: Evidence | ModelCallProof) -> bool:
        if not self.enabled:
            return False
        if self._finished or self._process is None or self._queue is None:
            raise ValueError("writer_not_started")
        raw = record.model_dump_json().encode()
        if not record.synthetic or len(raw) > 65536 or self._offered >= self.max_records:
            self._fault = "writer_message_budget_exceeded"
            self._dropped += 1
            return False
        if not self._process.is_alive():
            self._fault = "writer_child_unavailable"
            self._dropped += 1
            return False
        try:
            self._queue.put_nowait(raw)
        except Full:
            self._fault = "queue_backpressure_loss"
            self._dropped += 1
            return False
        self._offered += 1
        return True

    def finish(self, *, timeout_seconds: float = 5) -> WriterResult:
        if not 0 < timeout_seconds <= 15:
            raise ValueError("writer_shutdown_timeout_invalid")
        if self._finished:
            raise ValueError("writer_already_finished")
        self._finished = True
        if not self.enabled:
            return WriterResult(
                "disabled", "disabled", 0, 0, 0, None, os.getpid(), True, self.queue_capacity
            )
        process = self._process
        if process is None or self._queue is None or self._result_queue is None:
            return WriterResult(
                "blocked",
                "writer_not_started",
                0,
                0,
                0,
                None,
                os.getpid(),
                True,
                self.queue_capacity,
            )
        deadline = time.monotonic() + timeout_seconds
        persisted = 0
        model_calls = 0
        reason = self._fault or "completed"
        try:
            self._queue.put(None, timeout=max(0.001, deadline - time.monotonic()))
            persisted, model_calls, child_reason = self._result_queue.get(
                timeout=max(0.001, deadline - time.monotonic())
            )
            if child_reason != "completed":
                reason = child_reason
            process.join(timeout=max(0.001, deadline - time.monotonic()))
        except (Full, Empty, OSError, ValueError):
            reason = "writer_completion_unconfirmed"
        if process.is_alive():
            try:
                process.terminate()
                process.join(timeout=0.5)
            except (OSError, ValueError):
                reason = "shutdown_incomplete"
            if process.is_alive():
                try:
                    process.kill()
                    process.join(timeout=0.5)
                except (OSError, ValueError):
                    reason = "shutdown_incomplete"
            if reason != "shutdown_incomplete":
                reason = "writer_shutdown_forced"
        complete = not process.is_alive()
        if not complete:
            reason = "shutdown_incomplete"
        elif process.exitcode != 0 and reason == "completed":
            reason = "writer_child_failed"
        if persisted != self._offered and reason == "completed":
            reason = "writer_record_count_mismatch"
        for queue in (self._queue, self._result_queue):
            queue.cancel_join_thread()
            queue.close()
        return WriterResult(
            "passed" if reason == "completed" and complete else "blocked",
            reason,
            self._offered,
            persisted,
            self._dropped,
            process.pid,
            os.getpid(),
            complete,
            self.queue_capacity,
            model_calls_persisted=model_calls,
        )
