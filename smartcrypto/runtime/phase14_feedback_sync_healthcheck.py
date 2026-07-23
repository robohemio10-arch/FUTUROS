from __future__ import annotations

import argparse
import json
import os
import sqlite3
import stat
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_REPORT_PATH = Path(
    "/app/data/reports/phase14_runtime_feedback_sync_report.json"
)
DEFAULT_SNAPSHOT_PATH = Path(
    "/app/data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite"
)
DEFAULT_MAX_AGE_SECONDS = 300
DEFAULT_INSTANCE_TOLERANCE_SECONDS = 5
DEFAULT_FUTURE_TOLERANCE_SECONDS = 5

SAFE_FLAGS = {
    "paper_only": True,
    "shadow_only": True,
    "live_trading_enabled": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
    "write_performed": False,
    "private_endpoints_used": False,
}


class Phase14HealthcheckError(RuntimeError):
    """Controlled fail-closed local readiness error."""


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise Phase14HealthcheckError("naive_datetime_forbidden")
    return value.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return _ensure_utc(value).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise Phase14HealthcheckError("created_at_missing_or_invalid")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise Phase14HealthcheckError("created_at_missing_or_invalid") from exc
    try:
        return _ensure_utc(parsed)
    except Phase14HealthcheckError as exc:
        raise Phase14HealthcheckError("created_at_missing_or_invalid") from exc


def _read_pid1_started_at(
    *,
    proc_stat_path: Path,
    pid1_stat_path: Path,
    clock_ticks_per_second: int | None,
) -> datetime:
    try:
        proc_stat = proc_stat_path.read_text(encoding="utf-8")
        pid_stat = pid1_stat_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise Phase14HealthcheckError("proc_unavailable") from exc

    boot_times = [
        line.split(maxsplit=1)[1]
        for line in proc_stat.splitlines()
        if line.startswith("btime ") and len(line.split(maxsplit=1)) == 2
    ]
    closing_parenthesis = pid_stat.rfind(")")
    if len(boot_times) != 1:
        raise Phase14HealthcheckError("proc_boot_time_invalid")
    if closing_parenthesis < 0:
        raise Phase14HealthcheckError("proc_pid1_stat_invalid")
    fields = pid_stat[closing_parenthesis + 1 :].split()
    if len(fields) <= 19:
        raise Phase14HealthcheckError("proc_pid1_stat_invalid")

    sysconf = getattr(os, "sysconf", None)
    if clock_ticks_per_second is None and not callable(sysconf):
        raise Phase14HealthcheckError("proc_clock_ticks_unavailable")
    try:
        if clock_ticks_per_second is not None:
            ticks = int(clock_ticks_per_second)
        elif callable(sysconf):
            ticks = int(sysconf("SC_CLK_TCK"))
        else:
            raise Phase14HealthcheckError(
                "proc_clock_ticks_unavailable"
            )
        boot_time = int(boot_times[0])
        start_ticks = int(fields[19])
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise Phase14HealthcheckError("proc_timing_invalid") from exc
    if ticks <= 0 or boot_time <= 0 or start_ticks < 0:
        raise Phase14HealthcheckError("proc_timing_invalid")
    try:
        return datetime.fromtimestamp(
            boot_time + (start_ticks / ticks),
            tz=timezone.utc,
        )
    except (OSError, OverflowError, ValueError) as exc:
        raise Phase14HealthcheckError("proc_timing_invalid") from exc


def _read_report(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise Phase14HealthcheckError("report_symlink_forbidden")
    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError as exc:
        raise Phase14HealthcheckError("report_missing") from exc
    except OSError as exc:
        raise Phase14HealthcheckError("report_unreadable") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise Phase14HealthcheckError("report_not_regular_file")
    if metadata.st_size <= 0:
        raise Phase14HealthcheckError("report_empty")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Phase14HealthcheckError("report_invalid_json") from exc
    if not isinstance(payload, dict):
        raise Phase14HealthcheckError("report_not_object")
    return payload


def _report_contract_findings(report: Mapping[str, Any]) -> list[str]:
    expected: dict[str, Any] = {
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
    }
    findings = [
        f"report_field_invalid:{field}"
        for field, value in expected.items()
        if report.get(field) != value
    ]
    trade_link = report.get("decision_ledger_trade_link")
    if not isinstance(trade_link, Mapping):
        findings.append("decision_ledger_trade_link_invalid")
        return findings
    expected_link = {
        "status": "disabled",
        "enabled": False,
        "writer_invoked": False,
        "writes_runtime": False,
        "writes_sqlite": False,
    }
    findings.extend(
        f"decision_ledger_trade_link_field_invalid:{field}"
        for field, value in expected_link.items()
        if trade_link.get(field) != value
    )
    return findings


def _snapshot_findings(path: Path) -> tuple[list[str], bool, bool]:
    findings: list[str] = []
    readonly_open_ok = False
    trades_table_present = False
    if path.is_symlink():
        return ["snapshot_symlink_forbidden"], False, False
    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return ["snapshot_missing"], False, False
    except OSError:
        return ["snapshot_unreadable"], False, False
    if not stat.S_ISREG(metadata.st_mode):
        return ["snapshot_not_regular_file"], False, False
    if metadata.st_size <= 0:
        return ["snapshot_empty"], False, False

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=5,
        )
        readonly_open_ok = True
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='trades'"
        ).fetchone()
        trades_table_present = row is not None
        if not trades_table_present:
            findings.append("snapshot_trades_table_missing")
    except sqlite3.Error:
        findings.append("snapshot_readonly_open_failed")
    finally:
        if connection is not None:
            connection.close()

    deterministic_temp = path.with_suffix(path.suffix + ".tmp")
    if deterministic_temp.exists() or deterministic_temp.is_symlink():
        findings.append("deterministic_snapshot_temp_residue_present")
    try:
        exclusive_residues = tuple(
            path.parent.glob(f".{path.name}.*.tmp")
        )
    except OSError:
        findings.append("snapshot_temp_residue_scan_failed")
    else:
        if exclusive_residues:
            findings.append("exclusive_snapshot_temp_residue_present")
    return findings, readonly_open_ok, trades_table_present


def run_phase14_feedback_sync_healthcheck(
    *,
    report_path: str | Path = DEFAULT_REPORT_PATH,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    now: datetime | None = None,
    pid1_started_at: datetime | None = None,
    proc_stat_path: str | Path = "/proc/stat",
    pid1_stat_path: str | Path = "/proc/1/stat",
    clock_ticks_per_second: int | None = None,
    instance_tolerance_seconds: int = DEFAULT_INSTANCE_TOLERANCE_SECONDS,
    future_tolerance_seconds: int = DEFAULT_FUTURE_TOLERANCE_SECONDS,
) -> dict[str, Any]:
    checked_at = _ensure_utc(now or datetime.now(timezone.utc))
    report_target = Path(report_path)
    snapshot_target = Path(snapshot_path)
    findings: list[str] = []
    report: dict[str, Any] = {}
    created_at: datetime | None = None
    process_started_at: datetime | None = None
    report_belongs_to_current_instance = False

    if max_age_seconds <= 0:
        findings.append("max_age_seconds_invalid")
    if instance_tolerance_seconds < 0 or future_tolerance_seconds < 0:
        findings.append("timestamp_tolerance_invalid")

    try:
        report = _read_report(report_target)
    except Phase14HealthcheckError as exc:
        findings.append(str(exc))
    else:
        findings.extend(_report_contract_findings(report))
        try:
            created_at = _parse_timestamp(report.get("created_at"))
        except Phase14HealthcheckError as exc:
            findings.append(str(exc))

    try:
        process_started_at = _ensure_utc(
            pid1_started_at
            if pid1_started_at is not None
            else _read_pid1_started_at(
                proc_stat_path=Path(proc_stat_path),
                pid1_stat_path=Path(pid1_stat_path),
                clock_ticks_per_second=clock_ticks_per_second,
            )
        )
    except Phase14HealthcheckError as exc:
        findings.append(str(exc))

    snapshot_findings, readonly_open_ok, trades_table_present = _snapshot_findings(
        snapshot_target
    )
    findings.extend(snapshot_findings)

    if process_started_at is not None:
        if process_started_at > checked_at + timedelta(seconds=future_tolerance_seconds):
            findings.append("pid1_started_at_in_future")

    report_age_seconds: float | None = None
    if created_at is not None and process_started_at is not None:
        instance_floor = process_started_at - timedelta(
            seconds=instance_tolerance_seconds
        )
        future_limit = checked_at + timedelta(seconds=future_tolerance_seconds)
        report_belongs_to_current_instance = instance_floor <= created_at <= future_limit
        if created_at < instance_floor:
            findings.append("report_not_from_current_instance")
        if created_at > future_limit:
            findings.append("report_created_at_in_future")
        report_age_seconds = (checked_at - created_at).total_seconds()
        if max_age_seconds > 0 and report_age_seconds > max_age_seconds:
            findings.append("report_stale")

    blocking_findings = sorted(set(findings))
    status = "blocked" if blocking_findings else "ok"
    return {
        "status": status,
        "reason": (
            "phase14_feedback_sync_ready"
            if status == "ok"
            else ";".join(blocking_findings)
        ),
        "checked_at_utc": _iso_utc(checked_at),
        "report_path": str(report_target),
        "snapshot_path": str(snapshot_target),
        "report_created_at": _iso_utc(created_at) if created_at is not None else None,
        "report_age_seconds": report_age_seconds,
        "pid1_started_at": (
            _iso_utc(process_started_at) if process_started_at is not None else None
        ),
        "report_belongs_to_current_instance": report_belongs_to_current_instance,
        "snapshot_readonly_open_ok": readonly_open_ok,
        "snapshot_trades_table_present": trades_table_present,
        "blocking_findings": blocking_findings,
        **SAFE_FLAGS,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail-closed readiness check for Phase 14 paper feedback sync."
    )
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT_PATH))
    parser.add_argument("--max-age-seconds", type=int, default=DEFAULT_MAX_AGE_SECONDS)
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_phase14_feedback_sync_healthcheck(
        report_path=args.report,
        snapshot_path=args.snapshot,
        max_age_seconds=args.max_age_seconds,
    )
    if not args.quiet:
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
