from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import freqtrade_paper_healthcheck as healthcheck


NOW = datetime.fromtimestamp(1100, tz=timezone.utc)


def write_proc(tmp_path: Path, *, start_ticks: int = 500) -> tuple[Path, Path, Path]:
    proc_stat = tmp_path / "proc-stat"
    pid_stat = tmp_path / "pid1-stat"
    cmdline = tmp_path / "pid1-cmdline"
    proc_stat.write_text("cpu 1 2 3\nbtime 1000\n", encoding="utf-8")
    fields = ["S", *(["0"] * 18), str(start_ticks)]
    pid_stat.write_text(f"1 (freqtrade) {' '.join(fields)}\n", encoding="utf-8")
    return proc_stat, pid_stat, cmdline


def write_database(path: Path, *, with_trades: bool = True) -> None:
    connection = sqlite3.connect(path)
    try:
        if with_trades:
            connection.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY)")
        else:
            connection.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()


def valid_fixture(tmp_path: Path) -> dict[str, object]:
    config = tmp_path / "config.paper.json"
    database = tmp_path / "tradesv3.paper.sqlite"
    proc_stat, pid_stat, cmdline = write_proc(tmp_path)
    config.write_text(json.dumps({"dry_run": True}), encoding="utf-8")
    write_database(database)
    cmdline.write_bytes(
        b"/usr/local/bin/freqtrade\0trade\0--config\0"
        + str(config).encode()
        + b"\0--db-url\0sqlite:///"
        + str(database).encode()
        + b"\0"
    )
    return {
        "config_path": config,
        "database_path": database,
        "now": NOW,
        "proc_stat_path": proc_stat,
        "pid1_stat_path": pid_stat,
        "pid1_cmdline_path": cmdline,
        "clock_ticks_per_second": 100,
        "process_alive": True,
    }


def test_ready_payload_is_current_instance_read_only_and_safe(tmp_path: Path) -> None:
    report = healthcheck.run_freqtrade_paper_healthcheck(**valid_fixture(tmp_path))

    assert report["status"] == "ok"
    assert report["reason"] == "freqtrade_paper_ready"
    assert report["process_uptime_seconds"] == 95.0
    assert report["process_command_ok"] is True
    assert report["database_readonly_open_ok"] is True
    assert report["trades_table_present"] is True
    assert report["dry_run"] is True
    assert report["blocking_findings"] == []
    assert report["write_performed"] is False
    assert report["sends_orders"] is False
    assert report["exchange_private_access"] is False


def test_insufficient_uptime_is_blocked(tmp_path: Path) -> None:
    fixture = valid_fixture(tmp_path)
    fixture["min_uptime_seconds"] = 120

    report = healthcheck.run_freqtrade_paper_healthcheck(**fixture)

    assert report["status"] == "blocked"
    assert "process_uptime_insufficient" in report["blocking_findings"]


def test_invalid_proc_and_dead_pid_are_blocked(tmp_path: Path) -> None:
    fixture = valid_fixture(tmp_path)
    Path(fixture["pid1_stat_path"]).write_text("invalid", encoding="utf-8")
    fixture["process_alive"] = False

    report = healthcheck.run_freqtrade_paper_healthcheck(**fixture)

    assert "pid1_not_alive" in report["blocking_findings"]
    assert "proc_pid1_stat_invalid" in report["blocking_findings"]


def test_wrong_pid1_command_is_blocked(tmp_path: Path) -> None:
    fixture = valid_fixture(tmp_path)
    Path(fixture["pid1_cmdline_path"]).write_bytes(b"python\0worker.py\0")

    report = healthcheck.run_freqtrade_paper_healthcheck(**fixture)

    assert "pid1_command_not_freqtrade_paper_worker" in report["blocking_findings"]


@pytest.mark.parametrize(
    ("mutation", "finding"),
    [
        ("missing", "database_missing"),
        ("empty", "database_empty"),
        ("invalid", "database_readonly_open_failed"),
        ("no_trades", "trades_table_missing"),
    ],
)
def test_database_failures_are_blocked(
    tmp_path: Path,
    mutation: str,
    finding: str,
) -> None:
    fixture = valid_fixture(tmp_path)
    database = Path(fixture["database_path"])
    database.unlink()
    if mutation == "empty":
        database.touch()
    elif mutation == "invalid":
        database.write_text("not sqlite", encoding="utf-8")
    elif mutation == "no_trades":
        write_database(database, with_trades=False)

    report = healthcheck.run_freqtrade_paper_healthcheck(**fixture)

    assert finding in report["blocking_findings"]


def test_database_symlink_is_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = valid_fixture(tmp_path)
    database = Path(fixture["database_path"])
    original = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda self: self == database or original(self),
    )

    report = healthcheck.run_freqtrade_paper_healthcheck(**fixture)

    assert "database_symlink_forbidden" in report["blocking_findings"]


@pytest.mark.parametrize(
    ("config_value", "finding"),
    [
        (None, "config_missing"),
        ("invalid", "config_invalid_json"),
        (False, "paper_config_dry_run_not_true"),
    ],
)
def test_config_failures_are_blocked(
    tmp_path: Path,
    config_value: object,
    finding: str,
) -> None:
    fixture = valid_fixture(tmp_path)
    config = Path(fixture["config_path"])
    config.unlink()
    if config_value == "invalid":
        config.write_text("{", encoding="utf-8")
    elif config_value is False:
        config.write_text(json.dumps({"dry_run": False}), encoding="utf-8")

    report = healthcheck.run_freqtrade_paper_healthcheck(**fixture)

    assert finding in report["blocking_findings"]


def test_cli_quiet_and_exit_codes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        healthcheck,
        "run_freqtrade_paper_healthcheck",
        lambda **_: {"status": "ok"},
    )
    assert healthcheck.main(["--quiet"]) == 0
    assert capsys.readouterr().out == ""

    monkeypatch.setattr(
        healthcheck,
        "run_freqtrade_paper_healthcheck",
        lambda **_: {"status": "blocked"},
    )
    assert healthcheck.main(["--quiet"]) == 1
    assert capsys.readouterr().out == ""


def test_healthcheck_source_has_no_write_network_or_operational_imports() -> None:
    source = Path(healthcheck.__file__).read_text(encoding="utf-8")

    for token in (
        "write_text(",
        "write_bytes(",
        "urlopen(",
        "requests",
        "ccxt",
        "create_order(",
        "fetch_balance(",
    ):
        assert token not in source
