from __future__ import annotations

import argparse
import json
import os
import stat
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_REPORT_PATH = Path(
    "data/reports/qlib_paper_refresh_supervisor_report.json"
)
DEFAULT_PREDICTIONS_PATH = Path(
    "data/predictions/latest_qlib_predictions.parquet"
)
DEFAULT_MARKET_FEATURES_PATH = Path(
    "data/features/market_features_60d.parquet"
)
DEFAULT_MAX_AGE_SECONDS = 420
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


class QlibHealthcheckError(RuntimeError):
    """Controlled invalid input or local evidence error."""


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise QlibHealthcheckError("naive_datetime_forbidden")
    return value.astimezone(timezone.utc)


def iso_utc(value: datetime) -> str:
    return ensure_utc(value).isoformat().replace("+00:00", "Z")


def parse_utc_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise QlibHealthcheckError("generated_at_missing_or_invalid")
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise QlibHealthcheckError("generated_at_missing_or_invalid") from exc
    try:
        return ensure_utc(parsed)
    except QlibHealthcheckError as exc:
        raise QlibHealthcheckError("generated_at_missing_or_invalid") from exc


def read_pid1_started_at(
    *,
    proc_stat_path: str | Path = "/proc/stat",
    proc_pid1_stat_path: str | Path = "/proc/1/stat",
    clock_ticks_per_second: int | None = None,
) -> datetime:
    try:
        proc_stat = Path(proc_stat_path).read_text(encoding="utf-8")
        pid_stat = Path(proc_pid1_stat_path).read_text(encoding="utf-8")
    except OSError as exc:
        raise QlibHealthcheckError("proc_unavailable") from exc

    boot_values = [
        line.split(maxsplit=1)[1]
        for line in proc_stat.splitlines()
        if line.startswith("btime ") and len(line.split(maxsplit=1)) == 2
    ]
    if len(boot_values) != 1:
        raise QlibHealthcheckError("proc_boot_time_invalid")

    closing_parenthesis = pid_stat.rfind(")")
    if closing_parenthesis < 0:
        raise QlibHealthcheckError("proc_pid1_stat_invalid")
    remaining_fields = pid_stat[closing_parenthesis + 1 :].split()
    if len(remaining_fields) <= 19:
        raise QlibHealthcheckError("proc_pid1_stat_invalid")

    sysconf = getattr(os, "sysconf", None)
    if clock_ticks_per_second is None and not callable(sysconf):
        raise QlibHealthcheckError("proc_clock_ticks_unavailable")
    try:
        boot_time_seconds = int(boot_values[0])
        process_start_ticks = int(remaining_fields[19])
        if clock_ticks_per_second is None:
            if not callable(sysconf):
                raise QlibHealthcheckError("proc_clock_ticks_unavailable")
            ticks_per_second = int(sysconf("SC_CLK_TCK"))
        else:
            ticks_per_second = int(clock_ticks_per_second)
    except (OSError, TypeError, ValueError) as exc:
        raise QlibHealthcheckError("proc_timing_invalid") from exc
    if boot_time_seconds <= 0 or process_start_ticks < 0 or ticks_per_second <= 0:
        raise QlibHealthcheckError("proc_timing_invalid")

    started_at_seconds = boot_time_seconds + (process_start_ticks / ticks_per_second)
    try:
        return datetime.fromtimestamp(started_at_seconds, tz=timezone.utc)
    except (OSError, OverflowError, ValueError) as exc:
        raise QlibHealthcheckError("proc_timing_invalid") from exc


def read_report(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise QlibHealthcheckError("report_symlink_forbidden")
    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError as exc:
        raise QlibHealthcheckError("report_missing") from exc
    except OSError as exc:
        raise QlibHealthcheckError("report_unreadable") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise QlibHealthcheckError("report_not_regular_file")
    if metadata.st_size <= 0:
        raise QlibHealthcheckError("report_empty")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QlibHealthcheckError("report_invalid_json") from exc
    if not isinstance(payload, dict):
        raise QlibHealthcheckError("report_not_object")
    return payload


def artifact_findings(path: Path, *, label: str) -> list[str]:
    if path.is_symlink():
        return [f"{label}_symlink_forbidden"]
    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return [f"{label}_missing"]
    except OSError:
        return [f"{label}_unreadable"]
    if not stat.S_ISREG(metadata.st_mode):
        return [f"{label}_not_regular_file"]
    if metadata.st_size <= 0:
        return [f"{label}_empty"]
    return []


def report_contract_findings(report: Mapping[str, Any]) -> list[str]:
    expected_values: dict[str, Any] = {
        "status": "ok",
        "market_features_status": "ok",
        "predictions_status": "ok",
        "input_data_status": "input_data_fresh",
        "runtime_mode": "paper",
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
    }
    findings = [
        f"report_field_invalid:{field}"
        for field, expected in expected_values.items()
        if report.get(field) != expected
    ]
    if report.get("phase13_status") not in {"ok", "empty"}:
        findings.append("report_field_invalid:phase13_status")
    return findings


def run_qlib_refresh_supervisor_healthcheck(
    *,
    report_path: str | Path = DEFAULT_REPORT_PATH,
    predictions_path: str | Path = DEFAULT_PREDICTIONS_PATH,
    market_features_path: str | Path = DEFAULT_MARKET_FEATURES_PATH,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    now: datetime | None = None,
    pid1_started_at: datetime | None = None,
    proc_stat_path: str | Path = "/proc/stat",
    proc_pid1_stat_path: str | Path = "/proc/1/stat",
    clock_ticks_per_second: int | None = None,
    instance_tolerance_seconds: int = DEFAULT_INSTANCE_TOLERANCE_SECONDS,
    future_tolerance_seconds: int = DEFAULT_FUTURE_TOLERANCE_SECONDS,
) -> dict[str, Any]:
    checked_at = ensure_utc(now or datetime.now(timezone.utc))
    report_target = Path(report_path)
    predictions_target = Path(predictions_path)
    market_features_target = Path(market_features_path)
    findings: list[str] = []
    report: dict[str, Any] = {}
    report_generated_at: datetime | None = None
    process_started_at: datetime | None = None
    report_belongs_to_current_instance = False

    if max_age_seconds <= 0:
        findings.append("max_age_seconds_invalid")
    if instance_tolerance_seconds < 0 or future_tolerance_seconds < 0:
        findings.append("timestamp_tolerance_invalid")

    try:
        report = read_report(report_target)
    except QlibHealthcheckError as exc:
        findings.append(str(exc))
    else:
        findings.extend(report_contract_findings(report))
        try:
            report_generated_at = parse_utc_timestamp(report.get("generated_at"))
        except QlibHealthcheckError as exc:
            findings.append(str(exc))

    try:
        process_started_at = ensure_utc(
            pid1_started_at
            if pid1_started_at is not None
            else read_pid1_started_at(
                proc_stat_path=proc_stat_path,
                proc_pid1_stat_path=proc_pid1_stat_path,
                clock_ticks_per_second=clock_ticks_per_second,
            )
        )
    except QlibHealthcheckError as exc:
        findings.append(str(exc))

    findings.extend(artifact_findings(predictions_target, label="predictions"))
    findings.extend(artifact_findings(market_features_target, label="market_features"))

    if process_started_at is not None:
        future_limit = checked_at + timedelta(seconds=future_tolerance_seconds)
        if process_started_at > future_limit:
            findings.append("pid1_started_at_in_future")

    if report_generated_at is not None and process_started_at is not None:
        instance_floor = process_started_at - timedelta(seconds=instance_tolerance_seconds)
        future_limit = checked_at + timedelta(seconds=future_tolerance_seconds)
        report_belongs_to_current_instance = (
            instance_floor <= report_generated_at <= future_limit
        )
        if report_generated_at < instance_floor:
            findings.append("report_not_from_current_instance")
        if report_generated_at > future_limit:
            findings.append("report_generated_at_in_future")
        if max_age_seconds > 0:
            age_seconds = (checked_at - report_generated_at).total_seconds()
            if age_seconds > max_age_seconds:
                findings.append("report_stale")

    blocking_findings = sorted(set(findings))
    status = "blocked" if blocking_findings else "ok"
    reason = (
        "qlib_refresh_supervisor_ready"
        if status == "ok"
        else ";".join(blocking_findings)
    )
    return {
        "status": status,
        "reason": reason,
        "checked_at_utc": iso_utc(checked_at),
        "report_path": str(report_target),
        "predictions_path": str(predictions_target),
        "market_features_path": str(market_features_target),
        "report_generated_at": (
            iso_utc(report_generated_at) if report_generated_at is not None else None
        ),
        "pid1_started_at": (
            iso_utc(process_started_at) if process_started_at is not None else None
        ),
        "report_belongs_to_current_instance": report_belongs_to_current_instance,
        "blocking_findings": blocking_findings,
        **SAFE_FLAGS,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fail-closed readiness check for the paper Qlib refresh supervisor."
    )
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--predictions", default=str(DEFAULT_PREDICTIONS_PATH))
    parser.add_argument("--market-features", default=str(DEFAULT_MARKET_FEATURES_PATH))
    parser.add_argument("--max-age-seconds", type=int, default=DEFAULT_MAX_AGE_SECONDS)
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = run_qlib_refresh_supervisor_healthcheck(
            report_path=args.report,
            predictions_path=args.predictions,
            market_features_path=args.market_features,
            max_age_seconds=args.max_age_seconds,
        )
    except QlibHealthcheckError as exc:
        report = {
            "status": "blocked",
            "reason": str(exc),
            "checked_at_utc": iso_utc(datetime.now(timezone.utc)),
            "report_path": str(args.report),
            "predictions_path": str(args.predictions),
            "market_features_path": str(args.market_features),
            "report_generated_at": None,
            "pid1_started_at": None,
            "report_belongs_to_current_instance": False,
            "blocking_findings": [str(exc)],
            **SAFE_FLAGS,
        }
    if not args.quiet:
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
