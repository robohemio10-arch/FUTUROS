"""Strict read-only adapter to the existing canonical kill-switch authority."""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from smartcrypto.risk.kill_switch_guard import (
    DEFAULT_KILL_SWITCH_PATH,
    KillSwitchStateError,
    assert_local_regular_path,
    read_kill_switch_snapshot,
    require_symbol,
)
from smartcrypto.risk.kill_switch_guard import MAX_STATE_BYTES as MAX_STATE_BYTES

LEDGER_RELATIVE = Path("data/runtime/decision_ledger_paper_v1/decision_ledger_v4_2.jsonl")
POLL_SECONDS = 0.25
READ_DEADLINE_SECONDS = 0.75


class AuthorityDenied(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class AuthoritySnapshot:
    path: str
    sha256: str
    checked_at_utc: str
    updated_at_utc: str | None
    status: str
    blocked_symbols: tuple[str, ...]
    source_format: str = "structured_v1"


def _regular(path: Path, *, directory: bool = False) -> None:
    assert_local_regular_path(path, directory=directory)


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
        snapshot = read_kill_switch_snapshot(path)
        state = snapshot.state
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
            snapshot.sha256,
            datetime.now(timezone.utc).isoformat(),
            state["updated_at"],
            status,
            blocked,
            snapshot.source_format,
        )
    except AuthorityDenied:
        raise
    except KillSwitchStateError as exc:
        raise AuthorityDenied(exc.reason.replace("kill_switch_", "authority_", 1)) from exc
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
