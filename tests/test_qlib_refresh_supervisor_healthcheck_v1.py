from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from smartcrypto.runtime import qlib_refresh_supervisor_healthcheck as healthcheck


NOW = datetime(2026, 7, 22, 12, 1, tzinfo=timezone.utc)
PID1_STARTED_AT = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


def valid_report(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": "ok",
        "market_features_status": "ok",
        "predictions_status": "ok",
        "phase13_status": "ok",
        "input_data_status": "input_data_fresh",
        "runtime_mode": "paper",
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "generated_at": "2026-07-22T12:00:30Z",
    }
    payload.update(overrides)
    return payload


def write_evidence(
    tmp_path: Path,
    *,
    report: dict[str, object] | None = None,
) -> tuple[Path, Path, Path]:
    report_path = tmp_path / "supervisor.json"
    predictions_path = tmp_path / "latest_qlib_predictions.parquet"
    market_features_path = tmp_path / "market_features_60d.parquet"
    report_path.write_text(
        json.dumps(report if report is not None else valid_report()),
        encoding="utf-8",
    )
    predictions_path.write_bytes(b"PAR1-predictions")
    market_features_path.write_bytes(b"PAR1-features")
    return report_path, predictions_path, market_features_path


def run_check(
    report_path: Path,
    predictions_path: Path,
    market_features_path: Path,
    **overrides: object,
) -> dict[str, object]:
    arguments: dict[str, object] = {
        "report_path": report_path,
        "predictions_path": predictions_path,
        "market_features_path": market_features_path,
        "max_age_seconds": 420,
        "now": NOW,
        "pid1_started_at": PID1_STARTED_AT,
    }
    arguments.update(overrides)
    return healthcheck.run_qlib_refresh_supervisor_healthcheck(**arguments)  # type: ignore[arg-type]


def test_valid_report_from_current_instance_is_ready(tmp_path: Path) -> None:
    paths = write_evidence(tmp_path)

    payload = run_check(*paths)

    assert payload["status"] == "ok"
    assert payload["reason"] == "qlib_refresh_supervisor_ready"
    assert payload["report_belongs_to_current_instance"] is True
    assert payload["blocking_findings"] == []


def test_missing_report_is_blocked(tmp_path: Path) -> None:
    report_path, predictions_path, market_features_path = write_evidence(tmp_path)
    report_path.unlink()

    payload = run_check(report_path, predictions_path, market_features_path)

    assert payload["status"] == "blocked"
    assert "report_missing" in payload["blocking_findings"]


def test_invalid_report_json_is_blocked(tmp_path: Path) -> None:
    report_path, predictions_path, market_features_path = write_evidence(tmp_path)
    report_path.write_text("{invalid", encoding="utf-8")

    payload = run_check(report_path, predictions_path, market_features_path)

    assert "report_invalid_json" in payload["blocking_findings"]


def test_report_from_previous_instance_is_blocked(tmp_path: Path) -> None:
    paths = write_evidence(
        tmp_path,
        report=valid_report(generated_at="2026-07-22T11:59:50Z"),
    )

    payload = run_check(*paths)

    assert payload["report_belongs_to_current_instance"] is False
    assert "report_not_from_current_instance" in payload["blocking_findings"]


def test_report_too_far_in_future_is_blocked(tmp_path: Path) -> None:
    paths = write_evidence(
        tmp_path,
        report=valid_report(generated_at="2026-07-22T12:01:06Z"),
    )

    payload = run_check(*paths)

    assert payload["report_belongs_to_current_instance"] is False
    assert "report_generated_at_in_future" in payload["blocking_findings"]


def test_stale_report_is_blocked(tmp_path: Path) -> None:
    paths = write_evidence(
        tmp_path,
        report=valid_report(generated_at="2026-07-22T11:53:59Z"),
    )

    payload = run_check(
        *paths,
        pid1_started_at=NOW - timedelta(minutes=20),
    )

    assert "report_stale" in payload["blocking_findings"]


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    (
        ("status", "blocked"),
        ("market_features_status", "blocked"),
        ("predictions_status", "blocked"),
        ("phase13_status", "blocked"),
        ("input_data_status", "stale"),
        ("runtime_mode", "live"),
    ),
)
def test_invalid_operational_status_is_blocked(
    tmp_path: Path,
    field: str,
    invalid_value: str,
) -> None:
    paths = write_evidence(tmp_path, report=valid_report(**{field: invalid_value}))

    payload = run_check(*paths)

    assert payload["status"] == "blocked"
    assert f"report_field_invalid:{field}" in payload["blocking_findings"]


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    (
        ("shadow_only", False),
        ("live_trading_enabled", True),
        ("order_submission_enabled", True),
        ("real_order_submission_enabled", True),
        ("exchange_private_access", True),
    ),
)
def test_each_unsafe_report_flag_is_blocked(
    tmp_path: Path,
    field: str,
    unsafe_value: bool,
) -> None:
    paths = write_evidence(tmp_path, report=valid_report(**{field: unsafe_value}))

    payload = run_check(*paths)

    assert f"report_field_invalid:{field}" in payload["blocking_findings"]


def test_missing_predictions_is_blocked(tmp_path: Path) -> None:
    report_path, predictions_path, market_features_path = write_evidence(tmp_path)
    predictions_path.unlink()

    payload = run_check(report_path, predictions_path, market_features_path)

    assert "predictions_missing" in payload["blocking_findings"]


def test_empty_predictions_is_blocked(tmp_path: Path) -> None:
    report_path, predictions_path, market_features_path = write_evidence(tmp_path)
    predictions_path.write_bytes(b"")

    payload = run_check(report_path, predictions_path, market_features_path)

    assert "predictions_empty" in payload["blocking_findings"]


def test_predictions_symlink_is_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_path, predictions_path, market_features_path = write_evidence(tmp_path)
    original = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda self: self == predictions_path or original(self),
    )

    payload = run_check(report_path, predictions_path, market_features_path)

    assert "predictions_symlink_forbidden" in payload["blocking_findings"]


def test_missing_market_features_is_blocked(tmp_path: Path) -> None:
    report_path, predictions_path, market_features_path = write_evidence(tmp_path)
    market_features_path.unlink()

    payload = run_check(report_path, predictions_path, market_features_path)

    assert "market_features_missing" in payload["blocking_findings"]


@pytest.mark.parametrize("value", (None, "", "not-a-timestamp", "2026-07-22T12:00:30"))
def test_invalid_generated_at_is_blocked(tmp_path: Path, value: object) -> None:
    paths = write_evidence(tmp_path, report=valid_report(generated_at=value))

    payload = run_check(*paths)

    assert "generated_at_missing_or_invalid" in payload["blocking_findings"]


def test_invalid_proc_is_blocked(tmp_path: Path) -> None:
    paths = write_evidence(tmp_path)

    payload = healthcheck.run_qlib_refresh_supervisor_healthcheck(
        report_path=paths[0],
        predictions_path=paths[1],
        market_features_path=paths[2],
        now=NOW,
        proc_stat_path=tmp_path / "missing-proc-stat",
        proc_pid1_stat_path=tmp_path / "missing-pid-stat",
    )

    assert payload["status"] == "blocked"
    assert "proc_unavailable" in payload["blocking_findings"]


def test_pid1_start_is_calculated_from_proc_boot_time_and_ticks(tmp_path: Path) -> None:
    proc_stat = tmp_path / "stat"
    pid_stat = tmp_path / "pid1-stat"
    proc_stat.write_text("cpu 1 2 3 4\nbtime 1000\n", encoding="utf-8")
    remaining = ["S", *("0" for _ in range(18)), "250"]
    pid_stat.write_text(f"1 (paper supervisor) {' '.join(remaining)}\n", encoding="utf-8")

    started_at = healthcheck.read_pid1_started_at(
        proc_stat_path=proc_stat,
        proc_pid1_stat_path=pid_stat,
        clock_ticks_per_second=100,
    )

    assert started_at == datetime.fromtimestamp(1002.5, tz=timezone.utc)


def test_cli_quiet_returns_correct_exit_codes_without_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report_path, predictions_path, market_features_path = write_evidence(tmp_path)
    monkeypatch.setattr(
        healthcheck,
        "read_pid1_started_at",
        lambda **_: PID1_STARTED_AT,
    )
    monkeypatch.setattr(
        healthcheck,
        "datetime",
        type(
            "FixedDateTime",
            (datetime,),
            {"now": classmethod(lambda cls, tz=None: NOW)},
        ),
    )
    arguments = [
        "--quiet",
        "--report",
        str(report_path),
        "--predictions",
        str(predictions_path),
        "--market-features",
        str(market_features_path),
    ]

    assert healthcheck.main(arguments) == 0
    assert capsys.readouterr().out == ""
    predictions_path.unlink()
    assert healthcheck.main(arguments) == 1
    assert capsys.readouterr().out == ""


def test_payload_is_always_read_only_and_without_operational_authority(
    tmp_path: Path,
) -> None:
    payload = run_check(*write_evidence(tmp_path))

    assert payload["paper_only"] is True
    assert payload["shadow_only"] is True
    for field in (
        "live_trading_enabled",
        "live_release_allowed",
        "canary_release_allowed",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_risk",
        "write_performed",
        "private_endpoints_used",
    ):
        assert payload[field] is False
    assert list(tmp_path.glob("*.tmp")) == []
