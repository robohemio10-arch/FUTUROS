from __future__ import annotations

import argparse
import json
import os
import sqlite3
import stat
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path("/freqtrade/user_data/config.paper.json")
DEFAULT_DATABASE_PATH = Path("/freqtrade/user_data/db/tradesv3.paper.sqlite")
DEFAULT_MIN_UPTIME_SECONDS = 45

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


class FreqtradePaperHealthcheckError(RuntimeError):
    """Controlled fail-closed local readiness error."""


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise FreqtradePaperHealthcheckError("naive_datetime_forbidden")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_pid1_started_at(
    *,
    proc_stat_path: Path,
    pid1_stat_path: Path,
    clock_ticks_per_second: int | None = None,
) -> datetime:
    try:
        proc_stat = proc_stat_path.read_text(encoding="utf-8")
        pid_stat = pid1_stat_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FreqtradePaperHealthcheckError("proc_unavailable") from exc

    boot_times = [
        line.split(maxsplit=1)[1]
        for line in proc_stat.splitlines()
        if line.startswith("btime ") and len(line.split(maxsplit=1)) == 2
    ]
    closing_parenthesis = pid_stat.rfind(")")
    if len(boot_times) != 1:
        raise FreqtradePaperHealthcheckError("proc_boot_time_invalid")
    if closing_parenthesis < 0:
        raise FreqtradePaperHealthcheckError("proc_pid1_stat_invalid")

    remaining_fields = pid_stat[closing_parenthesis + 1 :].split()
    if len(remaining_fields) <= 19:
        raise FreqtradePaperHealthcheckError("proc_pid1_stat_invalid")

    sysconf = getattr(os, "sysconf", None)
    if clock_ticks_per_second is None and not callable(sysconf):
        raise FreqtradePaperHealthcheckError("proc_clock_ticks_unavailable")
    try:
        if clock_ticks_per_second is not None:
            ticks = int(clock_ticks_per_second)
        elif callable(sysconf):
            ticks = int(sysconf("SC_CLK_TCK"))
        else:
            raise FreqtradePaperHealthcheckError(
                "proc_clock_ticks_unavailable"
            )
        boot_time = int(boot_times[0])
        start_ticks = int(remaining_fields[19])
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise FreqtradePaperHealthcheckError("proc_timing_invalid") from exc
    if ticks <= 0 or boot_time <= 0 or start_ticks < 0:
        raise FreqtradePaperHealthcheckError("proc_timing_invalid")

    try:
        return datetime.fromtimestamp(
            boot_time + (start_ticks / ticks),
            tz=timezone.utc,
        )
    except (OSError, OverflowError, ValueError) as exc:
        raise FreqtradePaperHealthcheckError("proc_timing_invalid") from exc


def _read_pid1_command(path: Path) -> tuple[str, ...]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise FreqtradePaperHealthcheckError("proc_pid1_command_unavailable") from exc
    command = tuple(
        part.decode("utf-8", errors="replace")
        for part in raw.split(b"\0")
        if part
    )
    if not command:
        raise FreqtradePaperHealthcheckError("proc_pid1_command_invalid")
    return command


def _process_command_ok(
    command: Sequence[str],
    *,
    config_path: Path,
    database_path: Path,
) -> bool:
    lowered = tuple(part.lower() for part in command)
    executable_ok = any("freqtrade" in Path(part).name.lower() for part in command)
    worker_ok = "trade" in lowered
    config_ok = str(config_path).lower() in lowered
    database_ok = any(str(database_path).lower() in part for part in lowered)
    return executable_ok and worker_ok and config_ok and database_ok


def _read_config(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise FreqtradePaperHealthcheckError("config_symlink_forbidden")
    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError as exc:
        raise FreqtradePaperHealthcheckError("config_missing") from exc
    except OSError as exc:
        raise FreqtradePaperHealthcheckError("config_unreadable") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise FreqtradePaperHealthcheckError("config_not_regular_file")
    if metadata.st_size <= 0:
        raise FreqtradePaperHealthcheckError("config_empty")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FreqtradePaperHealthcheckError("config_invalid_json") from exc
    if not isinstance(payload, dict):
        raise FreqtradePaperHealthcheckError("config_not_object")
    return payload


def _database_findings(path: Path) -> tuple[list[str], bool]:
    findings: list[str] = []
    if path.is_symlink():
        return ["database_symlink_forbidden"], False
    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return ["database_missing"], False
    except OSError:
        return ["database_unreadable"], False
    if not stat.S_ISREG(metadata.st_mode):
        return ["database_not_regular_file"], False
    if metadata.st_size <= 0:
        return ["database_empty"], False

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=5,
        )
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='trades'"
        ).fetchone()
        if row is None:
            findings.append("trades_table_missing")
    except sqlite3.Error:
        findings.append("database_readonly_open_failed")
    finally:
        if connection is not None:
            connection.close()
    return findings, not findings


def run_freqtrade_paper_healthcheck(
    *,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    min_uptime_seconds: int = DEFAULT_MIN_UPTIME_SECONDS,
    now: datetime | None = None,
    proc_stat_path: str | Path = "/proc/stat",
    pid1_stat_path: str | Path = "/proc/1/stat",
    pid1_cmdline_path: str | Path = "/proc/1/cmdline",
    clock_ticks_per_second: int | None = None,
    process_alive: bool | None = None,
) -> dict[str, Any]:
    checked_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    config = Path(config_path)
    database = Path(database_path)
    findings: list[str] = []
    pid1_started_at: datetime | None = None
    uptime_seconds: float | None = None
    command_ok = False
    readonly_open_ok = False
    trades_table_present = False
    dry_run: bool | None = None

    if min_uptime_seconds < 0:
        findings.append("min_uptime_seconds_invalid")

    alive = process_alive
    if alive is None:
        try:
            os.kill(1, 0)
            alive = True
        except OSError:
            alive = False
    if not alive:
        findings.append("pid1_not_alive")

    try:
        pid1_started_at = _read_pid1_started_at(
            proc_stat_path=Path(proc_stat_path),
            pid1_stat_path=Path(pid1_stat_path),
            clock_ticks_per_second=clock_ticks_per_second,
        )
    except FreqtradePaperHealthcheckError as exc:
        findings.append(str(exc))
    else:
        uptime_seconds = (checked_at - pid1_started_at).total_seconds()
        if uptime_seconds < 0:
            findings.append("pid1_started_at_in_future")
        elif uptime_seconds < min_uptime_seconds:
            findings.append("process_uptime_insufficient")

    try:
        command = _read_pid1_command(Path(pid1_cmdline_path))
    except FreqtradePaperHealthcheckError as exc:
        findings.append(str(exc))
    else:
        command_ok = _process_command_ok(
            command,
            config_path=config,
            database_path=database,
        )
        if not command_ok:
            findings.append("pid1_command_not_freqtrade_paper_worker")

    try:
        config_payload = _read_config(config)
    except FreqtradePaperHealthcheckError as exc:
        findings.append(str(exc))
    else:
        dry_run = config_payload.get("dry_run")
        if dry_run is not True:
            findings.append("paper_config_dry_run_not_true")

    database_findings, readonly_open_ok = _database_findings(database)
    findings.extend(database_findings)
    trades_table_present = readonly_open_ok

    blocking_findings = sorted(set(findings))
    status = "blocked" if blocking_findings else "ok"
    return {
        "status": status,
        "reason": (
            "freqtrade_paper_ready"
            if status == "ok"
            else ";".join(blocking_findings)
        ),
        "checked_at_utc": _iso_utc(checked_at),
        "pid1_started_at": (
            _iso_utc(pid1_started_at) if pid1_started_at is not None else None
        ),
        "process_uptime_seconds": uptime_seconds,
        "process_command_ok": command_ok,
        "database_path": str(database),
        "database_readonly_open_ok": readonly_open_ok,
        "trades_table_present": trades_table_present,
        "dry_run": dry_run,
        "blocking_findings": blocking_findings,
        **SAFE_FLAGS,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail-closed local readiness check for Freqtrade paper."
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--database", default=str(DEFAULT_DATABASE_PATH))
    parser.add_argument(
        "--min-uptime-seconds",
        type=int,
        default=DEFAULT_MIN_UPTIME_SECONDS,
    )
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_freqtrade_paper_healthcheck(
        config_path=args.config,
        database_path=args.database,
        min_uptime_seconds=args.min_uptime_seconds,
    )
    if not args.quiet:
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
