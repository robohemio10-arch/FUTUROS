from __future__ import annotations

import errno
import os
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from scripts import export_freqtrade_paper_db_snapshot as exporter


RUNTIME_OUTPUT = Path(
    "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite"
)


def create_database(path: Path, trade_ids: tuple[int, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY)")
        connection.executemany(
            "INSERT INTO trades (id) VALUES (?)",
            ((trade_id,) for trade_id in trade_ids),
        )
        connection.commit()
    finally:
        connection.close()


def read_trade_ids_readonly(path: Path) -> list[int]:
    connection = sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro",
        uri=True,
    )
    try:
        rows = connection.execute("SELECT id FROM trades ORDER BY id").fetchall()
        return [int(row[0]) for row in rows]
    finally:
        connection.close()


def exclusive_residues(target: Path) -> list[Path]:
    return list(target.parent.glob(f".{target.name}.*.tmp"))


def test_runtime_equivalent_relative_output_is_exported_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "paper-db" / "tradesv3.paper.sqlite"
    physical_target = tmp_path / RUNTIME_OUTPUT
    create_database(source, (11, 12))

    report = exporter.export_local_sqlite_snapshot(source, RUNTIME_OUTPUT)

    assert report["status"] == "ok"
    assert report["source"] == str(source)
    assert report["output"] == str(RUNTIME_OUTPUT)
    assert report["parent_directory_fsync_status"] in {"ok", "unsupported"}
    assert read_trade_ids_readonly(physical_target) == [11, 12]
    assert not physical_target.with_suffix(physical_target.suffix + ".tmp").exists()
    assert exclusive_residues(physical_target) == []


def test_relative_source_and_relative_target_are_supported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    logical_source = Path("paper-db/tradesv3.paper.sqlite")
    create_database(tmp_path / logical_source, (21,))

    report = exporter.export_local_sqlite_snapshot(
        logical_source,
        RUNTIME_OUTPUT,
    )

    assert report["status"] == "ok"
    assert report["source"] == str(logical_source)
    assert report["output"] == str(RUNTIME_OUTPUT)
    assert read_trade_ids_readonly(tmp_path / RUNTIME_OUTPUT) == [21]


def test_explicit_working_directory_preserves_logical_paths(tmp_path: Path) -> None:
    logical_source = Path("paper-db/tradesv3.paper.sqlite")
    create_database(tmp_path / logical_source, (31,))

    report = exporter.export_local_sqlite_snapshot(
        logical_source,
        RUNTIME_OUTPUT,
        working_directory=tmp_path,
    )

    assert report["status"] == "ok"
    assert report["source"] == str(logical_source)
    assert report["output"] == str(RUNTIME_OUTPUT)
    assert read_trade_ids_readonly(tmp_path / RUNTIME_OUTPUT) == [31]


def test_absolute_mkstemp_inside_resolved_parent_is_accepted(
    tmp_path: Path,
) -> None:
    source = tmp_path / "paper-db" / "source.sqlite"
    create_database(source, (41,))
    observed: list[Path] = []

    def recording_mkstemp(**kwargs: object) -> tuple[int, str]:
        descriptor, raw_path = tempfile.mkstemp(**kwargs)
        observed.append(Path(raw_path))
        return descriptor, raw_path

    report = exporter.export_local_sqlite_snapshot(
        source,
        RUNTIME_OUTPUT,
        working_directory=tmp_path,
        mkstemp=recording_mkstemp,
    )

    physical_target = tmp_path / RUNTIME_OUTPUT
    assert report["status"] == "ok"
    assert len(observed) == 1
    assert observed[0].is_absolute()
    assert observed[0].parent.resolve() == physical_target.parent.resolve()
    assert exclusive_residues(physical_target) == []


def test_tempfile_outside_resolved_parent_is_blocked_and_owned_file_is_cleaned(
    tmp_path: Path,
) -> None:
    source = tmp_path / "paper-db" / "source.sqlite"
    target = tmp_path / RUNTIME_OUTPUT
    outside = tmp_path / "outside"
    outside.mkdir()
    create_database(source, (51,))
    create_database(target, (50,))
    created: list[Path] = []

    def external_mkstemp(**_kwargs: object) -> tuple[int, str]:
        path = outside / "owned-by-current-invocation.tmp"
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        created.append(path)
        return descriptor, str(path.resolve())

    report = exporter.export_local_sqlite_snapshot(
        source,
        RUNTIME_OUTPUT,
        working_directory=tmp_path,
        mkstemp=external_mkstemp,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "snapshot_tempfile_creation_failed"
    assert report["target_preserved_on_failure"] is True
    assert read_trade_ids_readonly(target) == [50]
    assert created and not created[0].exists()
    outside.rmdir()


def test_relative_target_is_preserved_when_backup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "paper-db" / "source.sqlite"
    target = tmp_path / RUNTIME_OUTPUT
    create_database(source, (61,))
    create_database(target, (60,))

    def fail_backup(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.DatabaseError("synthetic")

    monkeypatch.setattr(exporter, "_backup_to_owned_tempfile", fail_backup)
    report = exporter.export_local_sqlite_snapshot(
        source,
        RUNTIME_OUTPUT,
        working_directory=tmp_path,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "sqlite_backup_failed"
    assert report["target_preserved_on_failure"] is True
    assert read_trade_ids_readonly(target) == [60]
    assert exclusive_residues(target) == []


def test_relative_target_is_preserved_when_promotion_fails(tmp_path: Path) -> None:
    source = tmp_path / "paper-db" / "source.sqlite"
    target = tmp_path / RUNTIME_OUTPUT
    create_database(source, (71,))
    create_database(target, (70,))

    def deny_replace(*_args: object) -> None:
        raise PermissionError(errno.EACCES, "synthetic")

    report = exporter.export_local_sqlite_snapshot(
        source,
        RUNTIME_OUTPUT,
        working_directory=tmp_path,
        replace=deny_replace,
        sleep=lambda _delay: None,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "sqlite_backup_or_promotion_failed"
    assert report["target_preserved_on_failure"] is True
    assert read_trade_ids_readonly(target) == [70]
    assert exclusive_residues(target) == []


def test_concurrent_relative_outputs_use_distinct_owned_tempfiles(
    tmp_path: Path,
) -> None:
    source = tmp_path / "paper-db" / "source.sqlite"
    target = tmp_path / RUNTIME_OUTPUT
    create_database(source, (81, 82))
    observed: list[Path] = []

    def recording_mkstemp(**kwargs: object) -> tuple[int, str]:
        descriptor, raw_path = tempfile.mkstemp(**kwargs)
        observed.append(Path(raw_path))
        return descriptor, raw_path

    def run_export() -> dict[str, object]:
        return exporter.export_local_sqlite_snapshot(
            source,
            RUNTIME_OUTPUT,
            working_directory=tmp_path,
            mkstemp=recording_mkstemp,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        reports = list(pool.map(lambda _item: run_export(), range(2)))

    assert [report["status"] for report in reports] == ["ok", "ok"]
    assert len(observed) == 2
    assert len(set(observed)) == 2
    assert all(path.parent.resolve() == target.parent.resolve() for path in observed)
    assert read_trade_ids_readonly(target) == [81, 82]
    assert exclusive_residues(target) == []


def test_source_and_target_equivalent_after_resolution_is_blocked(
    tmp_path: Path,
) -> None:
    source = tmp_path / "data" / "source.sqlite"
    create_database(source, (91,))

    report = exporter.export_local_sqlite_snapshot(
        Path("data/../data/source.sqlite"),
        Path("data/source.sqlite"),
        working_directory=tmp_path,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "source_and_target_must_differ"
    assert read_trade_ids_readonly(source) == [91]


def test_docker_inline_uses_normalized_same_parent_atomic_contract(
    tmp_path: Path,
) -> None:
    command = exporter.build_docker_export_command(
        volume_name="paper-db",
        output=tmp_path / RUNTIME_OUTPUT,
        docker_image="python:test",
        volume_db_path="/paper-db/tradesv3.paper.sqlite",
    )
    inline = command[-1]

    for required in (
        "tempfile.mkstemp",
        "resolve(strict=False)",
        "temp_parent != target_parent",
        "mode=ro",
        "os.fsync",
        "os.replace",
        "cleanup_owned(temp_path)",
        f"range({exporter.TEMPFILE_ATTEMPTS})",
        f"range({exporter.REPLACE_ATTEMPTS})",
    ):
        assert required in inline

    for forbidden in (
        "target.with_suffix",
        "tmp.exists()",
        "tmp.unlink()",
        "temp_path.parent != target.parent",
    ):
        assert forbidden not in inline


def test_parent_directory_fsync_failure_is_not_reported_as_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "paper-db" / "source.sqlite"
    target = tmp_path / RUNTIME_OUTPUT
    create_database(source, (101,))

    def fail_parent_fsync(*_args: object, **_kwargs: object) -> str:
        raise OSError(errno.EIO, "synthetic")

    monkeypatch.setattr(exporter, "_fsync_parent_directory", fail_parent_fsync)
    report = exporter.export_local_sqlite_snapshot(
        source,
        RUNTIME_OUTPUT,
        working_directory=tmp_path,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "parent_directory_fsync_failed"
    assert report["parent_directory_fsync_status"] == "blocked"
    assert report["write_performed"] is True
    assert report["target_preserved_on_failure"] is False
    assert read_trade_ids_readonly(target) == [101]
    assert exclusive_residues(target) == []
