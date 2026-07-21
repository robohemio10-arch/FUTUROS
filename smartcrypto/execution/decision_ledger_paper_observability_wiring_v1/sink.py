"""Multiprocess-safe persistent idempotency sink for decision projections."""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from smartcrypto.execution.decision_ledger_runtime_profile_v1 import (
    RuntimeProjectionRecordV1,
)
from smartcrypto.execution.decision_ledger_v4_2 import DecisionLedgerWriter

from .contracts import SinkAppendReceiptV1

INDEX_SCHEMA_VERSION = "decision_ledger_projection_idempotency_index_v1"


class RuntimeSinkError(RuntimeError):
    """Base error for fail-visible sink failures."""


class CriticalIdempotencyConflict(RuntimeSinkError):
    """Same idempotency key observed with a different payload hash."""


class PersistentIndexError(RuntimeSinkError):
    """Persistent index is absent, malformed, or inconsistent."""


class IdempotentDecisionLedgerRuntimeSink:
    """Append through the certified writer with a durable projection index."""

    def __init__(
        self,
        *,
        writer: DecisionLedgerWriter,
        index_path: str | Path,
        lock_timeout_seconds: float = 2.0,
        fsync_enabled: bool = True,
    ) -> None:
        if lock_timeout_seconds <= 0:
            raise ValueError("index_lock_timeout_must_be_positive")
        self.writer = writer
        self.index_path = Path(index_path).expanduser().resolve(strict=False)
        self.lock_path = self.index_path.with_suffix(self.index_path.suffix + ".lock")
        self.lock_timeout_seconds = float(lock_timeout_seconds)
        self.fsync_enabled = fsync_enabled
        self._validate_path(self.index_path)
        self._validate_path(self.lock_path)
        if self.index_path.suffix != ".json":
            raise ValueError("index_path_must_be_json")

    def append(self, projection: RuntimeProjectionRecordV1) -> SinkAppendReceiptV1:
        target = projection.target_payload
        key = target.idempotency_key
        payload_hash = target.payload_sha256
        with self._exclusive_index_lock():
            index = self._load_index()
            entries = index["entries"]
            assert isinstance(entries, dict)
            previous = entries.get(key)
            if isinstance(previous, dict):
                self._validate_existing(previous, payload_hash)
                return self._receipt(projection, duplicate=True, appended=False, indexed=False)

            ledger_entries = self._scan_ledger()
            ledger_hash = ledger_entries.get(key)
            if ledger_hash is not None:
                if ledger_hash != payload_hash:
                    raise CriticalIdempotencyConflict(
                        f"ledger_idempotency_conflict:{key}"
                    )
                entries[key] = self._index_entry(projection)
                self._atomic_write_index(index)
                return self._receipt(projection, duplicate=True, appended=False, indexed=True)

            receipt = self.writer.append(target)
            if receipt.payload_sha256 != payload_hash:
                raise RuntimeSinkError("writer_receipt_payload_hash_mismatch")
            entries[key] = self._index_entry(projection)
            self._atomic_write_index(index)
            return self._receipt(projection, duplicate=False, appended=True, indexed=True)

    def read_index(self) -> dict[str, Any]:
        """Read a validated persisted index; no in-memory fallback exists."""

        return self._load_index()

    def _validate_path(self, path: Path) -> None:
        try:
            path.relative_to(self.writer.allowed_root)
        except ValueError as exc:
            raise ValueError(f"index_path_outside_writer_allowed_root:{path}") from exc

    @contextmanager
    def _exclusive_index_lock(self) -> Iterator[None]:
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
                    raise RuntimeSinkError(f"index_lock_timeout:{self.lock_path}")
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

    def _load_index(self) -> dict[str, Any]:
        if not self.index_path.exists():
            return {"schema_version": INDEX_SCHEMA_VERSION, "entries": {}}
        if self.index_path.is_symlink() or not self.index_path.is_file():
            raise PersistentIndexError("persistent_index_not_regular_file")
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PersistentIndexError(
                f"persistent_index_unreadable:{type(exc).__name__}"
            ) from exc
        if not isinstance(payload, dict):
            raise PersistentIndexError("persistent_index_root_invalid")
        if payload.get("schema_version") != INDEX_SCHEMA_VERSION:
            raise PersistentIndexError("persistent_index_schema_invalid")
        entries = payload.get("entries")
        if not isinstance(entries, dict):
            raise PersistentIndexError("persistent_index_entries_invalid")
        for key, entry in entries.items():
            if not isinstance(key, str) or not isinstance(entry, dict):
                raise PersistentIndexError("persistent_index_entry_invalid")
            if entry.get("idempotency_key") != key:
                raise PersistentIndexError("persistent_index_key_mismatch")
            if not _is_sha256(entry.get("payload_sha256")):
                raise PersistentIndexError("persistent_index_hash_invalid")
            if not isinstance(entry.get("projection"), dict):
                raise PersistentIndexError("persistent_index_projection_missing")
        return payload

    def _scan_ledger(self) -> dict[str, str]:
        path = self.writer.ledger_path
        if not path.exists():
            return {}
        if path.is_symlink() or not path.is_file():
            raise PersistentIndexError("ledger_not_regular_file")
        observed: dict[str, str] = {}
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            for line_number, line in enumerate(lines, start=1):
                if not line.strip():
                    continue
                payload = json.loads(line)
                key = str(payload["idempotency_key"])
                payload_hash = str(payload["payload_sha256"])
                previous = observed.get(key)
                if previous is not None and previous != payload_hash:
                    raise CriticalIdempotencyConflict(
                        f"ledger_conflict_at_line:{line_number}"
                    )
                observed[key] = payload_hash
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            raise PersistentIndexError(
                f"ledger_index_scan_failed:{type(exc).__name__}"
            ) from exc
        return observed

    def _atomic_write_index(self, payload: Mapping[str, Any]) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.index_path.with_name(
            f".{self.index_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        self._validate_path(temporary)
        encoded = (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        descriptor: int | None = None
        try:
            descriptor = os.open(
                temporary,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            offset = 0
            while offset < len(encoded):
                written = os.write(descriptor, encoded[offset:])
                if written <= 0:
                    raise OSError("index_write_returned_zero_bytes")
                offset += written
            if self.fsync_enabled:
                os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.replace(temporary, self.index_path)
            _fsync_directory(self.index_path.parent, enabled=self.fsync_enabled)
        except OSError as exc:
            raise PersistentIndexError(
                f"persistent_index_write_failed:{type(exc).__name__}"
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _validate_existing(entry: Mapping[str, Any], payload_hash: str) -> None:
        if entry.get("payload_sha256") != payload_hash:
            raise CriticalIdempotencyConflict(
                f"persistent_index_idempotency_conflict:{entry.get('idempotency_key')}"
            )

    @staticmethod
    def _index_entry(projection: RuntimeProjectionRecordV1) -> dict[str, Any]:
        target = projection.target_payload
        return {
            "idempotency_key": target.idempotency_key,
            "event_id": target.event_id,
            "record_type": target.record_type,
            "payload_sha256": target.payload_sha256,
            "projection": projection.model_dump(mode="json"),
        }

    @staticmethod
    def _receipt(
        projection: RuntimeProjectionRecordV1,
        *,
        duplicate: bool,
        appended: bool,
        indexed: bool,
    ) -> SinkAppendReceiptV1:
        target = projection.target_payload
        return SinkAppendReceiptV1(
            idempotency_key=target.idempotency_key,
            event_id=target.event_id,
            payload_sha256=target.payload_sha256,
            duplicate=duplicate,
            append_performed=appended,
            index_write_performed=indexed,
        )


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(character in "0123456789abcdef" for character in value)


def _fsync_directory(path: Path, *, enabled: bool) -> None:
    if not enabled or os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
