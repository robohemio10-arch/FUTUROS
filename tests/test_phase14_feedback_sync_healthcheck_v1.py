from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from smartcrypto.runtime import phase14_feedback_sync_healthcheck as healthcheck


NOW = datetime(2026, 7, 23, 14, 0, tzinfo=timezone.utc)
STARTED_AT = NOW - timedelta(minutes=10)


def valid_report(created_at: datetime = NOW - timedelta(seconds=30)) -> dict[str, object]:
    return {
        "status": "ok",
        "runtime_mode": "paper",
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "source_db_read_only": True,
        "dashboard_inputs_refreshed": True,
        "created_at": created_at.isoformat(),
        "decision_ledger_trade_link": {
            "status": "disabled",
            "enabled": False,
            "writer_invoked": False,
            "writes_runtime": False,
            "writes_sqlite": False,
        },
    }


def write_inputs(tmp_path: Path, report: dict[str, object] | None = None) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    report_path = tmp_path / "phase14.json"
    snapshot = tmp_path / "tradesv3.paper.snapshot.sqlite"
    report_path.write_text(
        json.dumps(report or valid_report()),
        encoding="utf-8",
    )
    connection = sqlite3.connect(snapshot)
    try:
        connection.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()
    return report_path, snapshot


def run(tmp_path: Path, report: dict[str, object] | None = None) -> dict[str, object]:
    report_path, snapshot = write_inputs(tmp_path, report)
    return healthcheck.run_phase14_feedback_sync_healthcheck(
        report_path=report_path,
        snapshot_path=snapshot,
        now=NOW,
        pid1_started_at=STARTED_AT,
    )


def test_current_report_and_snapshot_are_ready_read_only(tmp_path: Path) -> None:
    result = run(tmp_path)

    assert result["status"] == "ok"
    assert result["report_belongs_to_current_instance"] is True
    assert result["snapshot_readonly_open_ok"] is True
    assert result["snapshot_trades_table_present"] is True
    assert result["blocking_findings"] == []
    assert result["write_performed"] is False
    assert result["sends_orders"] is False


def test_report_from_previous_instance_is_blocked(tmp_path: Path) -> None:
    result = run(tmp_path, valid_report(STARTED_AT - timedelta(seconds=10)))

    assert "report_not_from_current_instance" in result["blocking_findings"]


def test_stale_and_future_reports_are_blocked(tmp_path: Path) -> None:
    stale = run(tmp_path / "stale", valid_report(NOW - timedelta(seconds=301)))
    future = run(tmp_path / "future", valid_report(NOW + timedelta(seconds=6)))

    assert "report_stale" in stale["blocking_findings"]
    assert "report_created_at_in_future" in future["blocking_findings"]


def test_invalid_proc_is_blocked(tmp_path: Path) -> None:
    report_path, snapshot = write_inputs(tmp_path)
    proc_stat = tmp_path / "proc-stat"
    pid_stat = tmp_path / "pid-stat"
    proc_stat.write_text("missing boot time", encoding="utf-8")
    pid_stat.write_text("invalid", encoding="utf-8")

    result = healthcheck.run_phase14_feedback_sync_healthcheck(
        report_path=report_path,
        snapshot_path=snapshot,
        now=NOW,
        proc_stat_path=proc_stat,
        pid1_stat_path=pid_stat,
        clock_ticks_per_second=100,
    )

    assert "proc_boot_time_invalid" in result["blocking_findings"]


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("status", "blocked"),
        ("runtime_mode", "live"),
        ("paper_only", False),
        ("shadow_only", False),
        ("live_trading_enabled", True),
        ("order_submission_enabled", True),
        ("real_order_submission_enabled", True),
        ("exchange_private_access", True),
        ("source_db_read_only", False),
        ("dashboard_inputs_refreshed", False),
    ],
)
def test_each_unsafe_report_flag_is_blocked(
    tmp_path: Path,
    path: str,
    value: object,
) -> None:
    report = valid_report()
    report[path] = value

    result = run(tmp_path, report)

    assert f"report_field_invalid:{path}" in result["blocking_findings"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "ok"),
        ("enabled", True),
        ("writer_invoked", True),
        ("writes_runtime", True),
        ("writes_sqlite", True),
    ],
)
def test_decision_ledger_must_remain_disabled(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    report = valid_report()
    trade_link = dict(report["decision_ledger_trade_link"])
    trade_link[field] = value
    report["decision_ledger_trade_link"] = trade_link

    result = run(tmp_path, report)

    assert (
        f"decision_ledger_trade_link_field_invalid:{field}"
        in result["blocking_findings"]
    )


@pytest.mark.parametrize(
    ("mutation", "finding"),
    [
        ("missing", "snapshot_missing"),
        ("empty", "snapshot_empty"),
        ("invalid", "snapshot_readonly_open_failed"),
        ("no_trades", "snapshot_trades_table_missing"),
    ],
)
def test_snapshot_failures_are_blocked(
    tmp_path: Path,
    mutation: str,
    finding: str,
) -> None:
    report_path, snapshot = write_inputs(tmp_path)
    snapshot.unlink()
    if mutation == "empty":
        snapshot.touch()
    elif mutation == "invalid":
        snapshot.write_text("invalid sqlite", encoding="utf-8")
    elif mutation == "no_trades":
        connection = sqlite3.connect(snapshot)
        try:
            connection.execute("CREATE TABLE unrelated (id INTEGER)")
            connection.commit()
        finally:
            connection.close()

    result = healthcheck.run_phase14_feedback_sync_healthcheck(
        report_path=report_path,
        snapshot_path=snapshot,
        now=NOW,
        pid1_started_at=STARTED_AT,
    )

    assert finding in result["blocking_findings"]


def test_snapshot_symlink_is_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_path, snapshot = write_inputs(tmp_path)
    original = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda self: self == snapshot or original(self),
    )

    result = healthcheck.run_phase14_feedback_sync_healthcheck(
        report_path=report_path,
        snapshot_path=snapshot,
        now=NOW,
        pid1_started_at=STARTED_AT,
    )

    assert "snapshot_symlink_forbidden" in result["blocking_findings"]


@pytest.mark.parametrize(
    ("residue_name", "finding"),
    [
        (
            "tradesv3.paper.snapshot.sqlite.tmp",
            "deterministic_snapshot_temp_residue_present",
        ),
        (
            ".tradesv3.paper.snapshot.sqlite.owned.tmp",
            "exclusive_snapshot_temp_residue_present",
        ),
    ],
)
def test_snapshot_temp_residues_are_blocked(
    tmp_path: Path,
    residue_name: str,
    finding: str,
) -> None:
    report_path, snapshot = write_inputs(tmp_path)
    (tmp_path / residue_name).write_text("residue", encoding="utf-8")

    result = healthcheck.run_phase14_feedback_sync_healthcheck(
        report_path=report_path,
        snapshot_path=snapshot,
        now=NOW,
        pid1_started_at=STARTED_AT,
    )

    assert finding in result["blocking_findings"]


def test_cli_quiet_and_exit_codes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        healthcheck,
        "run_phase14_feedback_sync_healthcheck",
        lambda **_: {"status": "ok"},
    )
    assert healthcheck.main(["--quiet"]) == 0
    assert capsys.readouterr().out == ""

    monkeypatch.setattr(
        healthcheck,
        "run_phase14_feedback_sync_healthcheck",
        lambda **_: {"status": "blocked"},
    )
    assert healthcheck.main(["--quiet"]) == 1
    assert capsys.readouterr().out == ""


def test_healthcheck_source_has_no_write_network_training_or_qlib_imports() -> None:
    source = Path(healthcheck.__file__).read_text(encoding="utf-8")

    for token in (
        "write_text(",
        "write_bytes(",
        "urlopen(",
        "requests",
        "ccxt",
        "to_parquet(",
        "smartcrypto.qlib",
        "create_order(",
    ):
        assert token not in source
