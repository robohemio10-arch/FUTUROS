"""Strict read-only adapter to the existing canonical kill-switch authority."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from smartcrypto.risk.kill_switch_guard import (
    DEFAULT_KILL_SWITCH_PATH,
    normalize_state,
    require_symbol,
)

LEDGER_RELATIVE = Path("data/runtime/decision_ledger_paper_v1/decision_ledger_v4_2.jsonl")
POLL_SECONDS = 0.25
READ_DEADLINE_SECONDS = 0.75
MAX_STATE_BYTES = 65536


class AuthorityDenied(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class AuthoritySnapshot:
    path: str
    sha256: str
    checked_at_utc: str
    updated_at_utc: str
    status: str
    blocked_symbols: tuple[str, ...]


def _regular(path: Path, *, directory: bool = False) -> None:
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024
        ):
            raise AuthorityDenied("authority_reparse_or_symlink_forbidden")
    mode = path.stat().st_mode
    if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
        raise AuthorityDenied("authority_regular_local_path_required")


def _utc(value: object) -> str:
    if not isinstance(value, str):
        raise AuthorityDenied("authority_explicit_utc_timestamp_required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise AuthorityDenied("authority_explicit_utc_timestamp_required")
    if parsed > datetime.now(timezone.utc):
        raise AuthorityDenied("authority_timestamp_in_future")
    return parsed.isoformat()


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AuthorityDenied("authority_duplicate_json_key")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise AuthorityDenied("authority_invalid_json_constant")


def _entry(value: object) -> None:
    if not isinstance(value, dict) or type(value.get("enabled")) is not bool:
        raise AuthorityDenied("authority_explicit_boolean_entry_required")
    reason = value.get("reason")
    actor = value.get("actor") or value.get("operator")
    for text in (reason, actor):
        if not isinstance(text, str) or not text.strip():
            raise AuthorityDenied("authority_explicit_entry_metadata_required")
    if value["reason"].strip().lower() == "default_clear":
        raise AuthorityDenied("authority_default_clear_not_authorization")
    timestamp = value.get("updated_at") or value.get("timestamp_utc")
    _utc(timestamp)
    if (
        value.get("updated_at")
        and value.get("timestamp_utc")
        and _utc(value["updated_at"]) != _utc(value["timestamp_utc"])
    ):
        raise AuthorityDenied("authority_conflicting_entry_timestamps")
    if value.get("actor") and value.get("operator") and value["actor"] != value["operator"]:
        raise AuthorityDenied("authority_conflicting_entry_actor")


def read_authority(
    runtime_root: Path | None, ledger: Path, symbols: tuple[str, ...]
) -> AuthoritySnapshot:
    """No Guard instance, default_state, evaluate, logger, or runtime writes."""
    try:
        if runtime_root is None or not runtime_root.is_absolute():
            raise AuthorityDenied("authority_runtime_root_unproven")
        # UNC/network filesystems are not a bounded local runtime authority.
        if str(runtime_root).startswith(("\\\\", "//")):
            raise AuthorityDenied("authority_local_runtime_root_required")
        _regular(runtime_root, directory=True)
        expected = runtime_root / LEDGER_RELATIVE
        if not ledger.is_absolute() or ledger != expected:
            raise AuthorityDenied("authority_ledger_runtime_binding_mismatch")
        _regular(ledger)
        if (
            not symbols
            or len(set(symbols)) != len(symbols)
            or any(require_symbol(symbol) != symbol for symbol in symbols)
        ):
            raise AuthorityDenied("authority_requested_symbols_invalid")
        path = runtime_root / DEFAULT_KILL_SWITCH_PATH
        _regular(path)
        before = path.stat()
        if before.st_size > MAX_STATE_BYTES:
            raise AuthorityDenied("authority_state_size_limit")
        with path.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            raw = handle.read(MAX_STATE_BYTES + 1)
            after_handle = os.fstat(handle.fileno())
        after = path.stat()
        signatures = {
            (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns)
            for row in (before, opened, after_handle, after)
        }
        # Windows stat/fstat expose different ctime semantics; compare each pair.
        if (
            len(signatures) != 1
            or len(raw) != before.st_size
            or before.st_ctime_ns != after.st_ctime_ns
            or opened.st_ctime_ns != after_handle.st_ctime_ns
        ):
            raise AuthorityDenied("authority_changed_during_read")
        payload = json.loads(raw, object_pairs_hook=_object, parse_constant=_invalid_constant)
        if (
            not isinstance(payload, dict)
            or type(payload.get("schema_version")) is not int
            or payload["schema_version"] != 1
            or payload.get("runtime_mode") != "paper"
            or "enabled" in payload
            or not isinstance(payload.get("symbols"), dict)
        ):
            raise AuthorityDenied("authority_explicit_schema_v1_paper_required")
        updated = _utc(payload.get("updated_at"))
        _entry(payload.get("global"))
        names: set[str] = set()
        for symbol, entry in payload["symbols"].items():
            normalized = require_symbol(symbol)
            if normalized in names:
                raise AuthorityDenied("authority_symbol_identity_collision")
            names.add(normalized)
            _entry(entry)
        # Validation above prevents the normalizer from manufacturing clear state.
        state = normalize_state(payload, "paper")
        blocked = tuple(
            symbol for symbol in symbols if state["symbols"].get(symbol, {}).get("enabled")
        )
        status = (
            "GLOBAL_BLOCKED"
            if state["global"]["enabled"]
            else "SYMBOL_BLOCKED"
            if blocked
            else "CLEAR"
        )
        return AuthoritySnapshot(
            str(path),
            hashlib.sha256(raw).hexdigest(),
            datetime.now(timezone.utc).isoformat(),
            updated,
            status,
            blocked,
        )
    except AuthorityDenied:
        raise
    except (OSError, ValueError, TypeError, RuntimeError, UnicodeError) as exc:
        raise AuthorityDenied(f"authority_read_{type(exc).__name__}") from exc


class AuthorityMonitor:
    """One bounded reader, latched denial, and fresh boundary checks with no retries.

    A stuck filesystem call cannot grant permission: its lease expires, callers
    time out, and a still-live reader is reported as incomplete shutdown.
    """

    def __init__(
        self,
        root: Path | None,
        ledger: Path,
        symbols: tuple[str, ...],
        on_denied: Callable[[str], None] | None = None,
    ) -> None:
        self.root, self.ledger, self.symbols = root, ledger, symbols
        self.on_denied = on_denied
        self.condition = threading.Condition()
        self.stopped = threading.Event()
        self.thread: threading.Thread | None = None
        self.snapshot: AuthoritySnapshot | None = None
        self.checked_monotonic = 0.0
        self.revision = 0
        self.failure: str | None = None

    def deny(self, reason: str) -> None:
        with self.condition:
            first = self.failure is None
            if first:
                self.failure = reason
            self.condition.notify_all()
        self.stopped.set()
        if first and self.on_denied is not None:
            self.on_denied(reason)

    def _monitor(self) -> None:
        try:
            while not self.stopped.is_set():
                started = time.monotonic()
                snapshot = read_authority(self.root, self.ledger, self.symbols)
                if time.monotonic() - started >= READ_DEADLINE_SECONDS:
                    raise AuthorityDenied("authority_read_deadline")
                with self.condition:
                    self.snapshot = snapshot
                    self.checked_monotonic = started
                    self.revision += 1
                    self.condition.notify_all()
                if snapshot.status != "CLEAR":
                    self.deny(f"authority_{snapshot.status.lower()}")
                    return
                # Fresh boundary checks wait for the next read; at most four/sec.
                if self.stopped.wait(POLL_SECONDS):
                    return
        except Exception as exc:
            self.deny(
                exc.reason
                if isinstance(exc, AuthorityDenied)
                else f"authority_monitor_{type(exc).__name__}"
            )

    def start(self) -> None:
        if self.thread is not None:
            raise AuthorityDenied("authority_monitor_single_use")
        self.thread = threading.Thread(
            target=self._monitor, name="l1-canonical-authority", daemon=True
        )
        try:
            self.thread.start()
        except RuntimeError as exc:
            self.deny("authority_monitor_start_failure")
            raise AuthorityDenied("authority_monitor_start_failure") from exc
        self.require(fresh=True)

    def require(self, *, fresh: bool = False) -> None:
        deadline = time.monotonic() + READ_DEADLINE_SECONDS
        with self.condition:
            revision = self.revision
            if self.thread is None:
                raise AuthorityDenied("authority_monitor_not_running")
            while fresh and self.revision <= revision and self.failure is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.condition.wait(remaining)
            reason = self.failure
            if reason is None and self.snapshot is not None and self.snapshot.status != "CLEAR":
                reason = f"authority_{self.snapshot.status.lower()}"
            if reason is None and (
                self.thread is None or not self.thread.is_alive() or self.stopped.is_set()
            ):
                reason = "authority_monitor_not_running"
            if reason is None and (
                self.snapshot is None
                or time.monotonic() - self.checked_monotonic >= READ_DEADLINE_SECONDS
                or (fresh and self.revision <= revision)
            ):
                reason = "authority_read_deadline"
        if reason is not None:
            self.deny(reason)
            raise AuthorityDenied(reason)

    def close(self, timeout: float) -> bool:
        self.stopped.set()
        if self.thread is not None and self.thread.ident is not None:
            self.thread.join(max(0, timeout))
        return self.thread is None or not self.thread.is_alive()

    def report(self) -> dict[str, Any]:
        with self.condition:
            return {
                "authority": "smartcrypto.risk.kill_switch_guard",
                "snapshot": asdict(self.snapshot) if self.snapshot else None,
                "reason": self.failure,
                "checks": self.revision,
                "poll_seconds": POLL_SECONDS,
                "read_deadline_seconds": READ_DEADLINE_SECONDS,
                "read_only": True,
                "event_log_writes": False,
                "shutdown_complete": self.thread is None or not self.thread.is_alive(),
            }


def inspect_authority(root: Path | None, ledger: Path, symbols: tuple[str, ...]) -> dict[str, Any]:
    monitor = AuthorityMonitor(root, ledger, symbols)
    try:
        monitor.start()
    finally:
        complete = monitor.close(READ_DEADLINE_SECONDS)
        if not complete:
            raise AuthorityDenied("authority_shutdown_incomplete")
    return monitor.report()
