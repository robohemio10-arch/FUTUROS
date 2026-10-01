"""Epoch/activation-scoped, serialized atomic append-by-identity research store."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import socket
import stat
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from smartcrypto.runtime.integrity_traceability_v2 import AtomicWritePolicy, atomic_write_json

from .activation import read_object, safe_path
from .contracts import EvidenceError, Identity, check_identity, digest

SCHEMA = "qlib_v3_prospective_evidence_store_v1"
GUARD_SCHEMA = "qlib_v3_store_guard_v1"
GUARD_TIMEOUT_SECONDS = 3.0
GUARD_POLL_SECONDS = 0.05
MAX_GUARD_BYTES = 4096
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class GuardReceipt:
    lock_id: str
    owner: str
    waited: bool
    wait_seconds: float
    contention_count: int
    acquired_at_utc: str

    @property
    def status(self) -> str:
        return "contention_recovered" if self.waited else "no_contention"


class StoreGuardAcquireTimeout(EvidenceError):
    reason_code = "store_guard_acquire_timeout"

    def __init__(self, wait_seconds: float, contention_count: int,
                 holder_metadata: dict[str, object] | None) -> None:
        super().__init__("store_busy_or_stale_guard_requires_review")
        self.wait_seconds = wait_seconds
        self.contention_count = contention_count
        self.holder_metadata = holder_metadata


def _validate_guard_args(owner: str, invocation_id: str,
                         timeout_seconds: float, poll_seconds: float) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", owner):
        raise EvidenceError("store_guard_owner_invalid")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", invocation_id):
        raise EvidenceError("store_guard_invocation_id_invalid")
    if (isinstance(timeout_seconds, bool) or isinstance(poll_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not isinstance(poll_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or not math.isfinite(poll_seconds)
            or not 0 < timeout_seconds <= 30 or not 0 < poll_seconds <= min(timeout_seconds, 1)):
        raise EvidenceError("store_guard_timing_invalid")


def _read_guard_bytes(guard: Path) -> tuple[bytes, os.stat_result]:
    safe_path(guard)
    flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0)
             | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
    fd = os.open(guard, flags)
    try:
        identity = os.fstat(fd)
        if not stat.S_ISREG(identity.st_mode):
            raise EvidenceError("store_guard_not_regular")
        chunks: list[bytes] = []
        remaining = MAX_GUARD_BYTES + 1
        while remaining:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks), identity
    finally:
        os.close(fd)


def read_guard_metadata(guard: Path) -> dict[str, object] | None:
    """Bounded, observational read; partial or legacy guards remain blocking."""
    try:
        raw, _ = _read_guard_bytes(guard)
        if len(raw) > MAX_GUARD_BYTES:
            return None
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get("schema_version") != GUARD_SCHEMA:
            return None
        if not isinstance(value.get("lock_id"), str):
            return None
        return value
    except (OSError, UnicodeError, ValueError, EvidenceError):
        return None


def _release_guard(guard: Path, expected: bytes, acquired_stat: os.stat_result) -> None:
    safe_path(guard)
    try:
        current, current_stat = _read_guard_bytes(guard)
        path_stat = guard.stat(follow_symlinks=False)
    except (OSError, EvidenceError) as exc:
        raise EvidenceError("store_guard_release_ownership_lost") from exc
    if ((current_stat.st_dev, current_stat.st_ino) !=
            (acquired_stat.st_dev, acquired_stat.st_ino)
            or (path_stat.st_dev, path_stat.st_ino) !=
            (acquired_stat.st_dev, acquired_stat.st_ino)
            or current != expected):
        raise EvidenceError("store_guard_release_ownership_lost")
    guard.unlink()


def location(root: Path, identity: Identity) -> Path:
    if not re.fullmatch(r"qlib-v3-[a-z0-9-]+", identity.epoch_id) or not re.fullmatch(
        r"[0-9a-f]{64}", identity.activation_sha256
    ):
        raise EvidenceError("unsafe_store_identity")
    return safe_path(root / "data/research/qlib_v3/prospective_evidence" /
                     identity.epoch_id / identity.activation_sha256 / "evidence.json")


def load(path: Path, identity: Identity) -> dict[str, Any]:
    safe_path(path)
    if not path.exists():
        return {"schema_version": SCHEMA, "identity": identity.mapping(),
                "signals": [], "outcomes": []}
    state = read_object(path)
    if state.get("schema_version") != SCHEMA:
        raise EvidenceError("store_not_v3")
    check_identity(state.get("identity"), identity)
    if state.get("store_sha256") != digest({k: v for k, v in state.items() if k != "store_sha256"}):
        raise EvidenceError("store_hash_mismatch")
    if not isinstance(state.get("signals"), list) or not isinstance(state.get("outcomes"), list):
        raise EvidenceError("store_records_invalid")
    if any(not isinstance(row, dict) for row in state["signals"] + state["outcomes"]):
        raise EvidenceError("store_record_not_object")
    return state


@contextmanager
def exclusive(path: Path, *, owner: str, invocation_id: str,
              timeout_seconds: float = GUARD_TIMEOUT_SECONDS,
              poll_seconds: float = GUARD_POLL_SECONDS) -> Iterator[GuardReceipt]:
    _validate_guard_args(owner, invocation_id, timeout_seconds, poll_seconds)
    safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_path(path.parent)
    guard = path.with_suffix(".guard")
    safe_path(guard)
    started = time.monotonic()
    contention_count = 0
    holder_metadata = None
    while True:
        try:
            fd = os.open(guard, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
            acquired_monotonic = time.monotonic()
            break
        except FileExistsError as exc:
            contention_count += 1
            holder_metadata = read_guard_metadata(guard)
            elapsed = time.monotonic() - started
            if elapsed >= timeout_seconds:
                LOGGER.warning("qlib_v3_store_guard_acquire_timeout owner=%s contention_count=%d",
                               owner, contention_count)
                raise StoreGuardAcquireTimeout(elapsed, contention_count, holder_metadata) from exc
            time.sleep(min(poll_seconds, timeout_seconds - elapsed))

    try:
        lock_id = uuid4().hex
        acquired_at_utc = datetime.now(UTC).isoformat()
        metadata = {
            "schema_version": GUARD_SCHEMA, "lock_id": lock_id, "owner": owner,
            "pid": os.getpid(), "hostname": socket.gethostname(),
            "acquired_at_utc": acquired_at_utc, "store_path": str(path.absolute()),
            "invocation_id": invocation_id,
        }
        encoded = (json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) > MAX_GUARD_BYTES:
            raise EvidenceError("store_guard_metadata_too_large")
        view = memoryview(encoded)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("store_guard_metadata_write_incomplete")
            view = view[written:]
        os.fsync(fd)
        acquired_stat = os.fstat(fd)
    except (OSError, EvidenceError, TypeError, ValueError) as exc:
        LOGGER.error("qlib_v3_store_guard_metadata_failed owner=%s error_type=%s", owner, type(exc).__name__)
        raise EvidenceError("store_guard_metadata_write_failed") from exc
    finally:
        os.close(fd)

    receipt = GuardReceipt(lock_id, owner, contention_count > 0,
                           acquired_monotonic - started, contention_count, acquired_at_utc)
    if receipt.waited:
        LOGGER.info("qlib_v3_store_guard_contention_recovered owner=%s wait_seconds=%.6f contention_count=%d",
                    owner, receipt.wait_seconds, contention_count)
    try:
        yield receipt
    finally:
        original_error = sys.exc_info()[0] is not None
        try:
            _release_guard(guard, encoded, acquired_stat)
        except (OSError, EvidenceError) as exc:
            LOGGER.error("qlib_v3_store_guard_release_failed owner=%s error_type=%s",
                         owner, type(exc).__name__)
            if not original_error:
                raise


def persist(path: Path, state: dict[str, Any]) -> None:
    safe_path(path)
    payload = {k: v for k, v in state.items() if k != "store_sha256"}
    payload["store_sha256"] = digest(payload)
    atomic_write_json(path, payload, allow_nan=False,
                      policy=AtomicWritePolicy.restricted([path.parent]))
