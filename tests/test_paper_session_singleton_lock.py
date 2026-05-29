import json
from pathlib import Path

from smartcrypto.runtime.paper_session_lock import PaperSessionLock


def _checker(live_pids: set[int]):
    return lambda pid: pid in live_pids


def test_creates_lock_when_missing(tmp_path: Path) -> None:
    lock_path = tmp_path / "paper_session.lock"
    lock = PaperSessionLock(lock_path, process_checker=_checker(set()))

    result = lock.acquire(pid=101, script="START_PAPER_24H.ps1", project_root=tmp_path, mode="paper-24h")

    assert result["status"] == "acquired"
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    assert payload["pid"] == 101
    assert payload["script"] == "START_PAPER_24H.ps1"
    assert payload["project_root"] == str(tmp_path)
    assert payload["mode"] == "paper-24h"
    assert payload["started_at"]


def test_blocks_second_acquire_when_pid_alive(tmp_path: Path) -> None:
    lock_path = tmp_path / "paper_session.lock"
    lock = PaperSessionLock(lock_path, process_checker=_checker({101}))
    assert lock.acquire(pid=101, script="START_PAPER_24H.ps1", project_root=tmp_path)["status"] == "acquired"

    result = lock.acquire(pid=202, script="START_PAPER_7D.ps1", project_root=tmp_path)

    assert result["status"] == "blocked"
    assert result["reason"] == "paper_session_already_active"
    assert json.loads(lock_path.read_text(encoding="utf-8"))["pid"] == 101


def test_removes_stale_lock_when_pid_dead(tmp_path: Path) -> None:
    lock_path = tmp_path / "paper_session.lock"
    lock = PaperSessionLock(lock_path, process_checker=_checker({202}))
    assert lock.acquire(pid=101, script="START_PAPER_24H.ps1", project_root=tmp_path)["status"] == "acquired"

    result = lock.acquire(pid=202, script="START_PAPER_7D.ps1", project_root=tmp_path, mode="paper-7d")

    assert result["status"] == "acquired"
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    assert payload["pid"] == 202
    assert payload["script"] == "START_PAPER_7D.ps1"
    assert payload["mode"] == "paper-7d"


def test_release_removes_own_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "paper_session.lock"
    lock = PaperSessionLock(lock_path, process_checker=_checker({101}))
    lock.acquire(pid=101, script="START_PAPER_24H.ps1", project_root=tmp_path)

    result = lock.release(pid=101, script="START_PAPER_24H.ps1")

    assert result["status"] == "released"
    assert not lock_path.exists()


def test_release_does_not_remove_other_process_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "paper_session.lock"
    lock = PaperSessionLock(lock_path, process_checker=_checker({101, 202}))
    lock.acquire(pid=101, script="START_PAPER_24H.ps1", project_root=tmp_path)

    result = lock.release(pid=202, script="START_PAPER_7D.ps1")

    assert result["status"] == "blocked"
    assert result["reason"] == "lock_owned_by_another_session"
    assert lock_path.exists()


def test_safe_cleanup_removes_only_stale_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "paper_session.lock"
    lock = PaperSessionLock(lock_path, process_checker=_checker(set()))
    lock.acquire(pid=101, script="START_PAPER_24H.ps1", project_root=tmp_path)

    result = lock.release(pid=999, script="STOP_PAPER_SESSION.ps1", cleanup_stale=True)

    assert result["status"] == "released"
    assert result["reason"] == "stale_lock_removed"
    assert not lock_path.exists()


def test_safe_cleanup_does_not_remove_live_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "paper_session.lock"
    lock = PaperSessionLock(lock_path, process_checker=_checker({101}))
    lock.acquire(pid=101, script="START_PAPER_24H.ps1", project_root=tmp_path)

    result = lock.release(pid=999, script="STOP_PAPER_SESSION.ps1", cleanup_stale=True)

    assert result["status"] == "blocked"
    assert lock_path.exists()


def test_inspect_returns_clear_active_and_stale_states(tmp_path: Path) -> None:
    lock_path = tmp_path / "paper_session.lock"
    live_pids = {101}
    lock = PaperSessionLock(lock_path, process_checker=_checker(live_pids))

    assert lock.inspect()["status"] == "clear"
    lock.acquire(pid=101, script="START_PAPER_24H.ps1", project_root=tmp_path)
    assert lock.inspect()["status"] == "active"
    live_pids.clear()
    stale = lock.inspect()
    assert stale["status"] == "stale"
    assert stale["reason"] == "stale_lock"


def test_lock_json_contains_required_fields(tmp_path: Path) -> None:
    lock_path = tmp_path / "paper_session.lock"
    lock = PaperSessionLock(lock_path, process_checker=_checker(set()))
    lock.acquire(pid=101, script="START_PAPER_24H.ps1", project_root=tmp_path, mode="paper-24h")

    payload = json.loads(lock_path.read_text(encoding="utf-8"))

    assert {"pid", "started_at", "script", "project_root", "mode"}.issubset(payload)
