"""Fail-visible append-only JSONL writer for decision-ledger payload 4.2."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from pydantic import BaseModel, ConfigDict, Field

from .contracts import PayloadRecordV42, parse_payload_record
from .serialization import canonical_json_bytes

HEALTH_SCHEMA_VERSION = "decision_ledger_writer_health_v1"
_FORBIDDEN_RUNTIME_SEQUENCES = (
    ("data", "runtime"),
    ("freqtrade", "user_data", "data", "runtime"),
)


class DecisionLedgerError(RuntimeError):
    """Base error for the isolated payload 4.2 writer."""


class RuntimePathDeniedError(DecisionLedgerError):
    """Raised when design-only mode is pointed at a runtime path."""


class LedgerLockError(DecisionLedgerError):
    """Raised when the cross-process lock cannot be acquired."""


class LedgerWriteError(DecisionLedgerError):
    """Raised when a JSONL append fails; the failure is never swallowed."""


class LedgerHealthError(DecisionLedgerError):
    """Raised when writer-health state cannot be read or atomically persisted."""


class LedgerHealthState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = HEALTH_SCHEMA_VERSION
    status: str
    ledger_path: str
    total_successes: int = Field(ge=0)
    total_failures: int = Field(ge=0)
    consecutive_failures: int = Field(ge=0)
    last_success_at_utc: str | None
    last_failure_at_utc: str | None
    last_event_id: str | None
    last_payload_sha256: str | None
    last_error_type: str | None
    last_error_message_sha256: str | None


@dataclass(frozen=True)
class AppendReceipt:
    event_id: str
    record_type: str
    payload_sha256: str
    bytes_written: int
    ledger_path: Path
    health_path: Path


class DecisionLedgerWriter:
    """Append sealed payloads and atomically maintain fail-visible health state."""

    def __init__(
        self,
        *,
        ledger_path: str | Path,
        health_path: str | Path,
        allowed_root: str | Path,
        design_only: bool = True,
        lock_timeout_seconds: float = 2.0,
        fsync_enabled: bool = True,
    ) -> None:
        if lock_timeout_seconds <= 0.0:
            raise ValueError("lock_timeout_seconds_must_be_positive")

        self.allowed_root = Path(allowed_root).expanduser().resolve(strict=False)
        self.ledger_path = Path(ledger_path).expanduser().resolve(strict=False)
        self.health_path = Path(health_path).expanduser().resolve(strict=False)
        self.design_only = design_only
        self.lock_timeout_seconds = float(lock_timeout_seconds)
        self.fsync_enabled = fsync_enabled
        self.lock_path = self.ledger_path.with_suffix(self.ledger_path.suffix + ".lock")

        self._validate_output_path(self.ledger_path)
        self._validate_output_path(self.health_path)
        self._validate_output_path(self.lock_path)

        if self.ledger_path == self.health_path:
            raise ValueError("ledger_path_and_health_path_must_differ")

    def append(self, record: PayloadRecordV42) -> AppendReceipt:
        """Append one sealed record and return a deterministic receipt."""

        verified = parse_payload_record(record.model_dump(mode="python"))
        line = canonical_json_bytes(verified, include_payload_hash=True) + b"\n"

        try:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            self.health_path.parent.mkdir(parents=True, exist_ok=True)
            with self._exclusive_lock():
                self._append_bytes(line)
                self._record_success(verified)
        except Exception as exc:
            if isinstance(exc, RuntimePathDeniedError):
                raise
            try:
                self._record_failure(exc)
            except Exception as health_exc:
                raise LedgerWriteError(
                    "ledger_append_failed_and_health_update_failed:"
                    f"{type(exc).__name__}:{type(health_exc).__name__}"
                ) from exc
            raise LedgerWriteError(
                f"ledger_append_failed:{type(exc).__name__}"
            ) from exc

        return AppendReceipt(
            event_id=verified.event_id,
            record_type=verified.record_type,
            payload_sha256=verified.payload_sha256,
            bytes_written=len(line),
            ledger_path=self.ledger_path,
            health_path=self.health_path,
        )

    def read_health(self) -> LedgerHealthState:
        """Read and validate the current health state, or return a zero state."""

        if not self.health_path.exists():
            return self._initial_health()

        try:
            payload = json.loads(self.health_path.read_text(encoding="utf-8"))
            return LedgerHealthState.model_validate(payload)
        except Exception as exc:
            raise LedgerHealthError(
                f"ledger_health_read_failed:{type(exc).__name__}"
            ) from exc

    def _validate_output_path(self, path: Path) -> None:
        try:
            path.relative_to(self.allowed_root)
        except ValueError as exc:
            raise RuntimePathDeniedError(
                f"path_outside_allowed_root:{path}"
            ) from exc

        if self.design_only and _contains_forbidden_runtime_sequence(path.parts):
            raise RuntimePathDeniedError(
                f"runtime_path_denied_in_design_only_mode:{path}"
            )

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        deadline = time.monotonic() + self.lock_timeout_seconds
        descriptor: int | None = None

        while descriptor is None:
            try:
                descriptor = os.open(
                    self.lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise LedgerLockError(f"ledger_lock_timeout:{self.lock_path}")
                time.sleep(0.01)

        try:
            os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii"))
            if self.fsync_enabled:
                os.fsync(descriptor)
            yield
        finally:
            os.close(descriptor)
            try:
                self.lock_path.unlink()
            except FileNotFoundError:
                pass

    def _append_bytes(self, payload: bytes) -> None:
        descriptor = os.open(
            self.ledger_path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
            0o600,
        )
        try:
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise OSError("ledger_write_returned_zero_bytes")
                offset += written
            if self.fsync_enabled:
                os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _record_success(self, record: PayloadRecordV42) -> None:
        current = self.read_health()
        now = _utc_now_iso()
        updated = LedgerHealthState(
            status="healthy",
            ledger_path=str(self.ledger_path),
            total_successes=current.total_successes + 1,
            total_failures=current.total_failures,
            consecutive_failures=0,
            last_success_at_utc=now,
            last_failure_at_utc=current.last_failure_at_utc,
            last_event_id=record.event_id,
            last_payload_sha256=record.payload_sha256,
            last_error_type=None,
            last_error_message_sha256=None,
        )
        self._atomic_write_health(updated)

    def _record_failure(self, error: Exception) -> None:
        current = self.read_health()
        message_hash = hashlib.sha256(str(error).encode("utf-8")).hexdigest()
        updated = LedgerHealthState(
            status="degraded",
            ledger_path=str(self.ledger_path),
            total_successes=current.total_successes,
            total_failures=current.total_failures + 1,
            consecutive_failures=current.consecutive_failures + 1,
            last_success_at_utc=current.last_success_at_utc,
            last_failure_at_utc=_utc_now_iso(),
            last_event_id=current.last_event_id,
            last_payload_sha256=current.last_payload_sha256,
            last_error_type=type(error).__name__,
            last_error_message_sha256=message_hash,
        )
        self._atomic_write_health(updated)

    def _initial_health(self) -> LedgerHealthState:
        return LedgerHealthState(
            status="initializing",
            ledger_path=str(self.ledger_path),
            total_successes=0,
            total_failures=0,
            consecutive_failures=0,
            last_success_at_utc=None,
            last_failure_at_utc=None,
            last_event_id=None,
            last_payload_sha256=None,
            last_error_type=None,
            last_error_message_sha256=None,
        )

    def _atomic_write_health(self, health: LedgerHealthState) -> None:
        self._validate_output_path(self.health_path)
        self.health_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.health_path.with_name(
            f".{self.health_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        self._validate_output_path(temporary_path)

        payload = json.dumps(
            health.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8") + b"\n"

        descriptor: int | None = None
        try:
            descriptor = os.open(
                temporary_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise OSError("health_write_returned_zero_bytes")
                offset += written
            if self.fsync_enabled:
                os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.replace(temporary_path, self.health_path)
            _fsync_directory(self.health_path.parent, enabled=self.fsync_enabled)
        except Exception as exc:
            raise LedgerHealthError(
                f"ledger_health_write_failed:{type(exc).__name__}"
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def _contains_forbidden_runtime_sequence(parts: tuple[str, ...]) -> bool:
    normalized = tuple(part.casefold() for part in parts)
    for sequence in _FORBIDDEN_RUNTIME_SEQUENCES:
        width = len(sequence)
        for index in range(0, len(normalized) - width + 1):
            if normalized[index : index + width] == sequence:
                return True
    return False


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _fsync_directory(path: Path, *, enabled: bool) -> None:
    if not enabled or os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
