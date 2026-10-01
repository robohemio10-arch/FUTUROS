"""Bounded, ownership-safe V3 store guard under real process contention."""

from __future__ import annotations

import json
import multiprocessing
import threading
import time
from pathlib import Path

import pytest

from smartcrypto.learning.qlib_v3_prospective import store
from smartcrypto.learning.qlib_v3_prospective.contracts import CANONICAL, EvidenceError


def _hold_guard(path: str, ready: object, release: object) -> None:
    with store.exclusive(Path(path), owner="qlib_v3_test_holder", invocation_id="holder"):
        ready.set()
        if not release.wait(10):
            raise RuntimeError("holder release timed out")


def _append_signal(path: str, signal_id: str, ready: object, start: object,
                   results: object) -> None:
    ready.put(signal_id)
    if not start.wait(10):
        raise RuntimeError("writer start timed out")
    target = Path(path)
    with store.exclusive(target, owner="qlib_v3_test_writer", invocation_id=signal_id) as receipt:
        state = store.load(target, CANONICAL)
        time.sleep(0.1)
        state["signals"].append({"signal_id": signal_id})
        store.persist(target, state)
        results.put(receipt.status)


def _state() -> dict[str, object]:
    return {"schema_version": store.SCHEMA, "identity": CANONICAL.mapping(),
            "signals": [], "outcomes": []}


def test_uncontended_receipt_and_fsynced_metadata(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    guard = path.with_suffix(".guard")
    with store.exclusive(path, owner="qlib_v3_test_writer", invocation_id="one") as receipt:
        metadata = store.read_guard_metadata(guard)
        assert metadata is not None
        assert metadata == json.loads(guard.read_text(encoding="utf-8"))
        assert metadata["schema_version"] == store.GUARD_SCHEMA
        assert metadata["lock_id"] == receipt.lock_id
        assert metadata["owner"] == receipt.owner
        assert metadata["invocation_id"] == "one"
        assert metadata["pid"] > 0
        assert metadata["hostname"]
        assert metadata["store_path"] == str(path.absolute())
        assert metadata["acquired_at_utc"] == receipt.acquired_at_utc
        assert receipt.status == "no_contention" and receipt.contention_count == 0
    assert not guard.exists()


def test_second_process_recovers_after_holder_releases(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    holder = context.Process(target=_hold_guard, args=(str(path), ready, release))
    holder.start()
    try:
        assert ready.wait(10)
        holder_metadata = store.read_guard_metadata(path.with_suffix(".guard"))
        assert holder_metadata is not None and holder_metadata["owner"] == "qlib_v3_test_holder"
        timer = threading.Timer(0.2, release.set)
        timer.start()
        try:
            with store.exclusive(path, owner="qlib_v3_test_writer", invocation_id="waiter",
                                 timeout_seconds=2, poll_seconds=0.02) as receipt:
                assert receipt.status == "contention_recovered"
                assert receipt.waited and receipt.wait_seconds > 0
                assert receipt.contention_count > 0
                store.persist(path, _state())
        finally:
            timer.join()
    finally:
        release.set()
        holder.join(10)
    assert holder.exitcode == 0
    assert store.load(path, CANONICAL)["store_sha256"]
    assert not path.with_suffix(".guard").exists()


def test_timeout_preserves_foreign_guard_and_store(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    store.persist(path, _state())
    before = path.read_bytes()
    guard = path.with_suffix(".guard")
    guard.write_bytes(b"orphan-or-unknown-holder")
    with pytest.raises(store.StoreGuardAcquireTimeout) as error:
        with store.exclusive(path, owner="qlib_v3_test_writer", invocation_id="waiter",
                             timeout_seconds=0.1, poll_seconds=0.02):
            pytest.fail("must not enter critical section")
    assert str(error.value) == "store_busy_or_stale_guard_requires_review"
    assert error.value.reason_code == "store_guard_acquire_timeout"
    assert error.value.contention_count > 0
    assert error.value.holder_metadata is None
    assert guard.read_bytes() == b"orphan-or-unknown-holder"
    assert path.read_bytes() == before


def test_non_regular_guard_fails_closed_without_removal(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    guard = path.with_suffix(".guard")
    guard.mkdir()
    with pytest.raises((store.StoreGuardAcquireTimeout, PermissionError)) as error:
        with store.exclusive(path, owner="qlib_v3_test_writer", invocation_id="waiter",
                             timeout_seconds=0.05, poll_seconds=0.01):
            pytest.fail("must not enter critical section")
    if isinstance(error.value, store.StoreGuardAcquireTimeout):
        assert error.value.holder_metadata is None
    assert guard.is_dir()


def test_timeout_exposes_holder_metadata_without_modifying_it(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    guard = path.with_suffix(".guard")
    with store.exclusive(path, owner="qlib_v3_test_holder", invocation_id="holder"):
        before = guard.read_bytes()
        with pytest.raises(store.StoreGuardAcquireTimeout) as error:
            with store.exclusive(path, owner="qlib_v3_test_writer", invocation_id="waiter",
                                 timeout_seconds=0.05, poll_seconds=0.01):
                pytest.fail("must not enter critical section")
        assert error.value.holder_metadata == json.loads(before)
        assert guard.read_bytes() == before


@pytest.mark.parametrize("field", ("lock_id", "owner"))
def test_changed_owner_or_lock_id_prevents_release(tmp_path: Path, field: str) -> None:
    path = tmp_path / "evidence.json"
    guard = path.with_suffix(".guard")
    with pytest.raises(EvidenceError, match="store_guard_release_ownership_lost"):
        with store.exclusive(path, owner="qlib_v3_test_writer", invocation_id="owner"):
            metadata = json.loads(guard.read_text(encoding="utf-8"))
            metadata[field] = "replacement"
            guard.write_text(json.dumps(metadata), encoding="utf-8")
    assert json.loads(guard.read_text(encoding="utf-8"))[field] == "replacement"


def test_missing_guard_preserves_original_error(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    guard = path.with_suffix(".guard")
    with pytest.raises(RuntimeError, match="body failed"):
        with store.exclusive(path, owner="qlib_v3_test_writer", invocation_id="owner"):
            guard.unlink()
            raise RuntimeError("body failed")
    assert not guard.exists()


def test_missing_guard_on_normal_exit_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    guard = path.with_suffix(".guard")
    with pytest.raises(EvidenceError, match="store_guard_release_ownership_lost"):
        with store.exclusive(path, owner="qlib_v3_test_writer", invocation_id="owner"):
            guard.unlink()


def test_metadata_fsync_failure_never_enters_critical_section(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "evidence.json"

    def fail_fsync(_fd: int) -> None:
        raise OSError("synthetic fsync error")

    monkeypatch.setattr(store.os, "fsync", fail_fsync)
    with pytest.raises(EvidenceError, match="store_guard_metadata_write_failed"):
        with store.exclusive(path, owner="qlib_v3_test_writer", invocation_id="owner"):
            pytest.fail("must not enter critical section")
    assert path.with_suffix(".guard").exists()
    assert not path.exists()


def test_invalid_timing_rejected_before_guard_creation(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    for timeout, poll in ((0, 0.01), (float("inf"), 0.01), (1, 0), (1, 2), (31, 1)):
        with pytest.raises(EvidenceError, match="store_guard_timing_invalid"):
            with store.exclusive(path, owner="qlib_v3_test_writer", invocation_id="timing",
                                 timeout_seconds=timeout, poll_seconds=poll):
                pytest.fail("must not enter critical section")
    assert not path.with_suffix(".guard").exists()


def test_two_process_writers_preserve_store_hash_and_both_identities(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    store.persist(path, _state())
    context = multiprocessing.get_context("spawn")
    ready, results, start = context.Queue(), context.Queue(), context.Event()
    workers = [context.Process(target=_append_signal,
                               args=(str(path), signal_id, ready, start, results))
               for signal_id in ("one", "two")]
    for worker in workers:
        worker.start()
    try:
        assert {ready.get(timeout=10), ready.get(timeout=10)} == {"one", "two"}
        start.set()
        assert {results.get(timeout=10), results.get(timeout=10)} == {
            "no_contention", "contention_recovered"}
    finally:
        start.set()
        for worker in workers:
            worker.join(10)
    assert all(worker.exitcode == 0 for worker in workers)
    state = store.load(path, CANONICAL)
    assert {row["signal_id"] for row in state["signals"]} == {"one", "two"}
    assert state["store_sha256"]
    assert not path.with_suffix(".guard").exists()
