from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


DEFAULT_LOCK_PATH = Path("data/runtime/paper_session.lock")


class PaperSessionLockError(RuntimeError):
    """Raised when a paper session singleton lock cannot be acquired or released."""


@dataclass(frozen=True)
class PaperSessionLockRecord:
    pid: int
    started_at: str
    script: str
    project_root: str
    mode: str


ProcessChecker = Callable[[int], bool]


def is_process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class PaperSessionLock:
    def __init__(
        self,
        lock_path: str | Path = DEFAULT_LOCK_PATH,
        *,
        process_checker: ProcessChecker = is_process_alive,
    ) -> None:
        self.lock_path = Path(lock_path)
        self.process_checker = process_checker

    def acquire(
        self,
        *,
        pid: int | None = None,
        script: str,
        project_root: str | Path,
        mode: str = "paper",
    ) -> dict[str, Any]:
        current_pid = int(pid if pid is not None else os.getpid())
        existing = self._read_lock()
        if existing is not None:
            existing_pid = _safe_int(existing.get("pid"))
            if existing_pid is not None and self.process_checker(existing_pid):
                return {
                    "status": "blocked",
                    "reason": "paper_session_already_active",
                    "lock_path": str(self.lock_path),
                    "active_lock": existing,
                }
            self._unlink_lock()

        record = PaperSessionLockRecord(
            pid=current_pid,
            started_at=datetime.now(timezone.utc).isoformat(),
            script=str(script),
            project_root=str(project_root),
            mode=str(mode),
        )
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text(json.dumps(asdict(record), indent=2, sort_keys=True), encoding="utf-8")
        return {
            "status": "acquired",
            "reason": None,
            "lock_path": str(self.lock_path),
            "lock": asdict(record),
        }

    def release(
        self,
        *,
        pid: int | None = None,
        script: str | None = None,
        cleanup_stale: bool = False,
    ) -> dict[str, Any]:
        existing = self._read_lock()
        if existing is None:
            return {"status": "missing", "reason": "lock_missing", "lock_path": str(self.lock_path)}

        existing_pid = _safe_int(existing.get("pid"))
        current_pid = int(pid if pid is not None else os.getpid())
        script_matches = script is None or str(existing.get("script")) == str(script)
        pid_matches = existing_pid == current_pid

        if pid_matches and script_matches:
            self._unlink_lock()
            return {"status": "released", "reason": None, "lock_path": str(self.lock_path)}

        if cleanup_stale and (existing_pid is None or not self.process_checker(existing_pid)):
            self._unlink_lock()
            return {"status": "released", "reason": "stale_lock_removed", "lock_path": str(self.lock_path)}

        return {
            "status": "blocked",
            "reason": "lock_owned_by_another_session",
            "lock_path": str(self.lock_path),
            "active_lock": existing,
        }

    def inspect(self) -> dict[str, Any]:
        existing = self._read_lock()
        if existing is None:
            return {"status": "clear", "reason": None, "lock_path": str(self.lock_path), "lock": None}
        pid = _safe_int(existing.get("pid"))
        alive = bool(pid is not None and self.process_checker(pid))
        return {
            "status": "active" if alive else "stale",
            "reason": None if alive else "stale_lock",
            "lock_path": str(self.lock_path),
            "pid_alive": alive,
            "lock": existing,
        }

    def _read_lock(self) -> dict[str, Any] | None:
        if not self.lock_path.exists():
            return None
        try:
            payload = json.loads(self.lock_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PaperSessionLockError(f"invalid paper session lock JSON: {self.lock_path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise PaperSessionLockError(f"invalid paper session lock payload: {self.lock_path}")
        return payload

    def _unlink_lock(self) -> None:
        self.lock_path.unlink(missing_ok=True)


def _safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _json_exit(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("status") in {"acquired", "released", "missing", "clear", "active", "stale"} else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage the SmartCrypto paper session singleton lock.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    acquire_parser = subparsers.add_parser("acquire")
    acquire_parser.add_argument("--lock-path", default=str(DEFAULT_LOCK_PATH))
    acquire_parser.add_argument("--pid", type=int, default=os.getpid())
    acquire_parser.add_argument("--script", required=True)
    acquire_parser.add_argument("--project-root", required=True)
    acquire_parser.add_argument("--mode", default="paper")

    release_parser = subparsers.add_parser("release")
    release_parser.add_argument("--lock-path", default=str(DEFAULT_LOCK_PATH))
    release_parser.add_argument("--pid", type=int, default=os.getpid())
    release_parser.add_argument("--script")
    release_parser.add_argument("--cleanup-stale", action="store_true")

    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("--lock-path", default=str(DEFAULT_LOCK_PATH))

    args = parser.parse_args(argv)
    lock = PaperSessionLock(args.lock_path)

    if args.command == "acquire":
        return _json_exit(lock.acquire(pid=args.pid, script=args.script, project_root=args.project_root, mode=args.mode))
    if args.command == "release":
        return _json_exit(lock.release(pid=args.pid, script=args.script, cleanup_stale=args.cleanup_stale))
    return _json_exit(lock.inspect())


if __name__ == "__main__":
    raise SystemExit(main())
