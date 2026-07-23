from __future__ import annotations

import errno
import os
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import export_freqtrade_paper_db_snapshot as exporter


def create_database(path: Path, value: int) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO trades (id) VALUES (?)", (value,))
        connection.commit()
    finally:
        connection.close()


def read_trade_id(path: Path) -> int:
    connection = sqlite3.connect(path)
    try:
        return int(connection.execute("SELECT id FROM trades").fetchone()[0])
    finally:
        connection.close()


def test_success_creates_new_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite"
    target = tmp_path / "snapshots" / "target.sqlite"
    create_database(source, 7)

    report = exporter.export_local_sqlite_snapshot(source, target)

    assert report["status"] == "ok"
    assert read_trade_id(target) == 7
    assert report["tempfile_strategy"] == "exclusive_same_directory"
    assert not target.with_suffix(target.suffix + ".tmp").exists()


def test_success_atomically_replaces_previous_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite"
    target = tmp_path / "target.sqlite"
    create_database(source, 8)
    create_database(target, 3)

    report = exporter.export_local_sqlite_snapshot(source, target)

    assert report["status"] == "ok"
    assert read_trade_id(target) == 8


def test_exporter_closes_source_and_destination_connections_on_windows(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.sqlite"
    target = tmp_path / "target.sqlite"
    create_database(source, 81)

    report = exporter.export_local_sqlite_snapshot(source, target)

    assert report["status"] == "ok"
    source.unlink()
    target.unlink()
    assert not source.exists()
    assert not target.exists()


def test_each_invocation_receives_unique_tempfile(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite"
    target = tmp_path / "target.sqlite"
    create_database(source, 9)
    observed: list[Path] = []

    def recording_mkstemp(**kwargs: object) -> tuple[int, str]:
        descriptor, raw_path = tempfile.mkstemp(**kwargs)
        observed.append(Path(raw_path))
        return descriptor, raw_path

    first = exporter.export_local_sqlite_snapshot(
        source,
        target,
        mkstemp=recording_mkstemp,
    )
    second = exporter.export_local_sqlite_snapshot(
        source,
        target,
        mkstemp=recording_mkstemp,
    )

    assert first["status"] == second["status"] == "ok"
    assert len(observed) == 2
    assert observed[0] != observed[1]
    assert all(path.name.startswith(f".{target.name}.") for path in observed)
    assert all(path.name.endswith(".tmp") for path in observed)


def test_concurrent_invocations_never_share_tempfile(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite"
    target = tmp_path / "target.sqlite"
    create_database(source, 91)
    observed: list[Path] = []

    def recording_mkstemp(**kwargs: object) -> tuple[int, str]:
        descriptor, raw_path = tempfile.mkstemp(**kwargs)
        observed.append(Path(raw_path))
        return descriptor, raw_path

    def export() -> dict[str, object]:
        return exporter.export_local_sqlite_snapshot(
            source,
            target,
            mkstemp=recording_mkstemp,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        reports = list(pool.map(lambda _: export(), range(2)))

    assert [report["status"] for report in reports] == ["ok", "ok"]
    assert len(observed) == 2
    assert len(set(observed)) == 2
    assert read_trade_id(target) == 91


def test_transient_tempfile_create_is_retried(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite"
    target = tmp_path / "target.sqlite"
    create_database(source, 10)
    calls = 0
    delays: list[float] = []

    def flaky_mkstemp(**kwargs: object) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError(errno.EACCES, "synthetic")
        return tempfile.mkstemp(**kwargs)

    report = exporter.export_local_sqlite_snapshot(
        source,
        target,
        mkstemp=flaky_mkstemp,
        sleep=delays.append,
    )

    assert report["status"] == "ok"
    assert report["tempfile_create_retry_count"] == 1
    assert calls == 2
    assert delays == [exporter.RETRY_BASE_DELAY_SECONDS]


def test_permanent_tempfile_create_failure_is_not_retried(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite"
    target = tmp_path / "target.sqlite"
    create_database(source, 11)
    calls = 0

    def fail(**_kwargs: object) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        raise OSError(errno.ENOSPC, "synthetic")

    report = exporter.export_local_sqlite_snapshot(
        source,
        target,
        mkstemp=fail,
        sleep=lambda _: None,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "snapshot_tempfile_creation_failed"
    assert calls == 1
    assert not target.exists()


def test_transient_replace_is_retried(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite"
    target = tmp_path / "target.sqlite"
    create_database(source, 12)
    calls = 0
    delays: list[float] = []

    def flaky_replace(source_path: os.PathLike[str], target_path: os.PathLike[str]) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError(errno.EACCES, "synthetic")
        os.replace(source_path, target_path)

    report = exporter.export_local_sqlite_snapshot(
        source,
        target,
        replace=flaky_replace,
        sleep=delays.append,
    )

    assert report["status"] == "ok"
    assert report["replace_retry_count"] == 1
    assert calls == 2
    assert read_trade_id(target) == 12


def test_replace_exhaustion_preserves_previous_target(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite"
    target = tmp_path / "target.sqlite"
    create_database(source, 13)
    create_database(target, 4)
    calls = 0

    def denied(*_args: object) -> None:
        nonlocal calls
        calls += 1
        raise PermissionError(errno.EACCES, "synthetic")

    report = exporter.export_local_sqlite_snapshot(
        source,
        target,
        replace=denied,
        sleep=lambda _: None,
    )

    assert report["status"] == "blocked"
    assert calls == exporter.REPLACE_ATTEMPTS
    assert read_trade_id(target) == 4
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []


def test_backup_failure_preserves_previous_target_and_unrelated_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.sqlite"
    target = tmp_path / "target.sqlite"
    sentinel = tmp_path / "unrelated.tmp"
    create_database(source, 14)
    create_database(target, 5)
    sentinel.write_text("preserve", encoding="utf-8")
    monkeypatch.setattr(
        exporter,
        "_backup_to_owned_tempfile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            sqlite3.DatabaseError("synthetic")
        ),
    )

    report = exporter.export_local_sqlite_snapshot(source, target)

    assert report["status"] == "blocked"
    assert report["reason"] == "sqlite_backup_failed"
    assert read_trade_id(target) == 5
    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []


def test_cleanup_failure_is_controlled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.sqlite"
    target = tmp_path / "target.sqlite"
    create_database(source, 15)
    monkeypatch.setattr(
        exporter,
        "_backup_to_owned_tempfile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            sqlite3.DatabaseError("synthetic")
        ),
    )
    monkeypatch.setattr(
        exporter,
        "_cleanup_owned_tempfile",
        lambda _path: "PermissionError:errno=13",
    )

    report = exporter.export_local_sqlite_snapshot(source, target)

    assert report["status"] == "blocked"
    assert report["reason"] == "snapshot_owned_tempfile_cleanup_failed"
    assert report["error"] == "PermissionError:errno=13"


def test_deterministic_tempfile_is_never_referenced_or_removed() -> None:
    source = Path(exporter.__file__).read_text(encoding="utf-8")

    assert "with_suffix(target.suffix + \".tmp\")" not in source
    assert "tempfile.mkstemp" in source
    assert "prefix=f\".{target.name}.\"" in source
    assert "os.replace" in source


def test_docker_inline_command_uses_same_safe_contract(tmp_path: Path) -> None:
    command = exporter.build_docker_export_command(
        volume_name="futuros_freqtrade_paper_db",
        output=tmp_path / "target.sqlite",
        docker_image="python:test",
        volume_db_path="/paper-db/tradesv3.paper.sqlite",
    )
    inline = command[-1]

    assert "tempfile.mkstemp" in inline
    assert "mode=ro" in inline
    assert "os.fsync" in inline
    assert "os.replace" in inline
    assert "target.with_suffix" not in inline
    assert "tmp.exists()" not in inline
    assert "tmp.unlink()" not in inline


def test_docker_volume_export_returns_controlled_report_without_real_docker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target.sqlite"
    report_path = tmp_path / "report.json"

    def fake_run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        target.write_bytes(b"snapshot")
        return SimpleNamespace(returncode=0, stdout='{"status":"ok"}', stderr="")

    monkeypatch.setattr(exporter.subprocess, "run", fake_run)

    report = exporter.export_docker_volume_snapshot(
        output=target,
        report_path=report_path,
    )

    assert report["status"] == "ok"
    assert report["source_db_read_only"] is True
    assert report["sends_orders"] is False
    assert report_path.exists()
