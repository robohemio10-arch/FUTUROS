from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

OK = "ok"
MISSING_SOURCE = "missing_source"
DB_STALE = "db_stale"
DB_IO_ERROR = "db_io_error"
LOG_SQLITE_DIVERGENCE = "log_sqlite_divergence"
INVALID_SCHEMA = "invalid_schema"

DEFAULT_DB_PATH = Path("freqtrade/user_data/tradesv3.paper.sqlite")
DEFAULT_REPORT_PATH = Path("data/reports/freqtrade_paper_db_persistence_audit.json")
DEFAULT_CONTAINER_NAME = "futuros-freqtrade-paper-1"
DEFAULT_CONTAINER_DB_PATH = "/freqtrade/user_data/tradesv3.paper.sqlite"
DEFAULT_LOG_CANDIDATES = (
    Path("freqtrade/user_data/logs/freqtrade-paper.log"),
    Path("data/evidence/db_persistence_fault_20260601/freqtrade_tail_1000.log"),
)

TIMESTAMP_RE = re.compile(r"(?P<ts>20\d{2}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:[,.](?P<fraction>\d{1,6}))?")
TRADE_ID_PATTERNS = (
    re.compile(r"['\"]trade_id['\"]\s*:\s*(?P<id>\d+)", re.IGNORECASE),
    re.compile(r"\btrade_id\s*[=:]\s*(?P<id>\d+)", re.IGNORECASE),
    re.compile(r"Trade\(id=(?P<id>\d+)", re.IGNORECASE),
    re.compile(r"Updating trade \(id=(?P<id>\d+)\)", re.IGNORECASE),
)


@dataclass(frozen=True)
class LogTradeObservation:
    latest_trade_id: int | None
    latest_trade_time_utc: str | None
    latest_trade_line: str | None
    observed_trade_ids: list[int]
    log_paths_read: list[str]
    log_paths_missing: list[str]
    log_read_errors: dict[str, str]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _path_info(path: Path) -> dict[str, Any]:
    payload: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return payload
    stat = path.stat()
    payload.update(
        {
            "size_bytes": int(stat.st_size),
            "mtime_epoch": float(stat.st_mtime),
            "mtime_utc": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        }
    )
    return payload


def _parse_log_timestamp(line: str) -> datetime | None:
    match = TIMESTAMP_RE.search(line)
    if not match:
        return None
    fraction = (match.group("fraction") or "0")[:6].ljust(6, "0")
    text = f"{match.group('ts')}.{fraction}"
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _extract_trade_ids(line: str) -> list[int]:
    values: list[int] = []
    for pattern in TRADE_ID_PATTERNS:
        for match in pattern.finditer(line):
            try:
                values.append(int(match.group("id")))
            except Exception:
                continue
    return values


def parse_freqtrade_logs(log_paths: Iterable[str | Path]) -> LogTradeObservation:
    latest_trade_id: int | None = None
    latest_trade_time: datetime | None = None
    latest_trade_line: str | None = None
    observed: set[int] = set()
    read: list[str] = []
    missing: list[str] = []
    errors: dict[str, str] = {}

    for item in log_paths:
        path = Path(item)
        if not path.exists():
            missing.append(str(path))
            continue
        read.append(str(path))
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for raw_line in handle:
                    line = raw_line.rstrip("\n")
                    ids = _extract_trade_ids(line)
                    if not ids:
                        continue
                    observed.update(ids)
                    line_ts = _parse_log_timestamp(line)
                    line_max = max(ids)

                    should_replace = False
                    if latest_trade_id is None or line_max > latest_trade_id:
                        should_replace = True
                    elif line_max == latest_trade_id and line_ts is not None:
                        should_replace = latest_trade_time is None or line_ts > latest_trade_time

                    if should_replace:
                        latest_trade_id = line_max
                        latest_trade_time = line_ts
                        latest_trade_line = line[:1200]
        except Exception as exc:
            errors[str(path)] = repr(exc)

    return LogTradeObservation(
        latest_trade_id=latest_trade_id,
        latest_trade_time_utc=latest_trade_time.isoformat() if latest_trade_time else None,
        latest_trade_line=latest_trade_line,
        observed_trade_ids=sorted(observed),
        log_paths_read=read,
        log_paths_missing=missing,
        log_read_errors=errors,
    )


def inspect_sqlite_db(db_path: str | Path) -> dict[str, Any]:
    path = Path(db_path)
    payload: dict[str, Any] = {
        "db": _path_info(path),
        "wal": _path_info(Path(str(path) + "-wal")),
        "shm": _path_info(Path(str(path) + "-shm")),
        "read_status": None,
        "read_error": None,
        "quick_check": None,
        "journal_mode": None,
        "tables": [],
        "trades_table_exists": False,
        "trade_count": None,
        "open_count": None,
        "closed_count": None,
        "max_id": None,
    }

    if not path.exists():
        payload["read_status"] = MISSING_SOURCE
        return payload

    try:
        with sqlite3.connect(str(path), timeout=10) as conn:
            cur = conn.cursor()

            try:
                payload["quick_check"] = cur.execute("pragma quick_check").fetchone()
            except Exception as exc:
                payload["quick_check"] = f"quick_check_error:{exc!r}"

            try:
                row = cur.execute("pragma journal_mode").fetchone()
                payload["journal_mode"] = row[0] if row else None
            except Exception as exc:
                payload["journal_mode"] = f"journal_mode_error:{exc!r}"

            tables = [str(row[0]) for row in cur.execute("select name from sqlite_master where type='table'").fetchall()]
            payload["tables"] = tables
            payload["trades_table_exists"] = "trades" in set(tables)

            if not payload["trades_table_exists"]:
                payload["read_status"] = INVALID_SCHEMA
                return payload

            count, open_count, closed_count, max_id = cur.execute(
                """
                select
                    count(*),
                    sum(case when is_open = 1 then 1 else 0 end),
                    sum(case when is_open = 0 then 1 else 0 end),
                    max(id)
                from trades
                """
            ).fetchone()

            payload.update(
                {
                    "trade_count": int(count or 0),
                    "open_count": int(open_count or 0),
                    "closed_count": int(closed_count or 0),
                    "max_id": int(max_id) if max_id is not None else None,
                    "read_status": OK,
                }
            )
            return payload

    except Exception as exc:
        payload["read_error"] = repr(exc)
        payload["read_status"] = DB_IO_ERROR if "disk I/O error" in str(exc).lower() else INVALID_SCHEMA
        return payload


def inspect_container_sqlite(
    *,
    container_name: str,
    container_db_path: str,
    enabled: bool,
) -> dict[str, Any]:
    if not enabled:
        return {"enabled": False, "status": "skipped", "reason": "container_read_not_requested"}

    probe = r'''
import json
import sqlite3
import sys
from pathlib import Path

p = Path(sys.argv[1])
out = {"path": str(p), "exists": p.exists(), "size_bytes": p.stat().st_size if p.exists() else None}
try:
    con = sqlite3.connect(str(p), timeout=10)
    cur = con.cursor()
    out["journal_mode"] = (cur.execute("pragma journal_mode").fetchone() or [None])[0]
    tables = [str(row[0]) for row in cur.execute("select name from sqlite_master where type='table'").fetchall()]
    out["tables"] = tables
    if "trades" not in set(tables):
        out["status"] = "invalid_schema"
    else:
        count, open_count, closed_count, max_id = cur.execute("""
            select
                count(*),
                sum(case when is_open = 1 then 1 else 0 end),
                sum(case when is_open = 0 then 1 else 0 end),
                max(id)
            from trades
        """).fetchone()
        out.update({
            "status": "ok",
            "trade_count": int(count or 0),
            "open_count": int(open_count or 0),
            "closed_count": int(closed_count or 0),
            "max_id": int(max_id) if max_id is not None else None,
        })
    con.close()
except Exception as exc:
    out["status"] = "db_io_error" if "disk I/O error" in str(exc).lower() else "error"
    out["error"] = repr(exc)
print(json.dumps(out, ensure_ascii=False, sort_keys=True))
'''

    try:
        completed = subprocess.run(
            ["docker", "exec", container_name, "python", "-c", probe, container_db_path],
            check=False,
            text=True,
            capture_output=True,
            timeout=20,
        )
    except Exception as exc:
        return {"enabled": True, "status": "error", "reason": "docker_exec_failed", "error": repr(exc)}

    payload: dict[str, Any] = {
        "enabled": True,
        "container_name": container_name,
        "container_db_path": container_db_path,
        "returncode": completed.returncode,
        "stderr": completed.stderr.strip()[:4000],
    }

    if completed.returncode != 0:
        payload["status"] = "container_unavailable_or_exec_failed"
        payload["stdout"] = completed.stdout.strip()[:4000]
        return payload

    try:
        parsed = json.loads(completed.stdout.strip())
        payload.update(parsed)
        return payload
    except Exception as exc:
        payload.update(
            {
                "status": "invalid_container_probe_output",
                "error": repr(exc),
                "stdout": completed.stdout.strip()[:4000],
            }
        )
        return payload


def _seconds_between_iso(left: str | None, right: str | None) -> float | None:
    if not left or not right:
        return None
    try:
        a = datetime.fromisoformat(left)
        b = datetime.fromisoformat(right)
        return (a - b).total_seconds()
    except Exception:
        return None


def classify_status(
    *,
    db_info: dict[str, Any],
    log_info: LogTradeObservation,
    stale_tolerance_seconds: int,
    container_info: dict[str, Any],
) -> tuple[str, str | None, list[str]]:
    reasons: list[str] = []
    db_status = db_info.get("read_status")

    if db_status == MISSING_SOURCE:
        reasons.append("sqlite_db_missing")
        return MISSING_SOURCE, "sqlite_db_missing", reasons

    if db_status == DB_IO_ERROR:
        reasons.append("sqlite_local_read_failed")
        return DB_IO_ERROR, "sqlite_local_read_failed", reasons

    if db_status == INVALID_SCHEMA:
        reasons.append("sqlite_schema_invalid_or_unreadable")
        return INVALID_SCHEMA, "sqlite_schema_invalid_or_unreadable", reasons

    container_status = str(container_info.get("status")) if container_info else "skipped"
    if container_status == DB_IO_ERROR:
        reasons.append("sqlite_container_read_failed_with_disk_io_error")
        return DB_IO_ERROR, "sqlite_container_read_failed_with_disk_io_error", reasons

    latest_trade_id = log_info.latest_trade_id
    max_id = db_info.get("max_id")
    if latest_trade_id is not None and max_id is not None and int(latest_trade_id) > int(max_id):
        reasons.append(f"latest_trade_id_from_log_gt_sqlite_max_id:{latest_trade_id}>{max_id}")
        return LOG_SQLITE_DIVERGENCE, "latest_trade_id_from_log_exceeds_sqlite_max_id", reasons

    db_mtime = db_info.get("db", {}).get("mtime_utc")
    age_delta = _seconds_between_iso(log_info.latest_trade_time_utc, db_mtime)
    if age_delta is not None and age_delta > stale_tolerance_seconds:
        reasons.append(f"db_mtime_older_than_latest_trade_log_by_seconds:{age_delta:.3f}")
        return DB_STALE, "db_mtime_older_than_latest_trade_log", reasons

    return OK, None, reasons


def audit_persistence(
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    log_paths: Iterable[str | Path] = DEFAULT_LOG_CANDIDATES,
    report_path: str | Path = DEFAULT_REPORT_PATH,
    stale_tolerance_seconds: int = 60,
    check_container_read: bool = False,
    container_name: str = DEFAULT_CONTAINER_NAME,
    container_db_path: str = DEFAULT_CONTAINER_DB_PATH,
) -> dict[str, Any]:
    db_info = inspect_sqlite_db(db_path)
    log_info = parse_freqtrade_logs(log_paths)
    container_info = inspect_container_sqlite(
        container_name=container_name,
        container_db_path=container_db_path,
        enabled=check_container_read,
    )

    status, reason, reasons = classify_status(
        db_info=db_info,
        log_info=log_info,
        stale_tolerance_seconds=stale_tolerance_seconds,
        container_info=container_info,
    )

    payload: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "reasons": reasons,
        "paper_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "db_path": str(db_path),
        "report_path": str(report_path),
        "db": db_info,
        "logs": {
            "latest_trade_id_from_log": log_info.latest_trade_id,
            "latest_trade_time_utc": log_info.latest_trade_time_utc,
            "latest_trade_line": log_info.latest_trade_line,
            "observed_trade_ids": log_info.observed_trade_ids,
            "log_paths_read": log_info.log_paths_read,
            "log_paths_missing": log_info.log_paths_missing,
            "log_read_errors": log_info.log_read_errors,
        },
        "container_read": container_info,
        "created_at": utc_now(),
    }

    write_json(Path(report_path), payload)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit read-only Freqtrade paper SQLite persistence against runtime logs.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Host-visible Freqtrade paper SQLite path.")
    parser.add_argument("--log", action="append", default=None, help="Freqtrade log path. Can be repeated.")
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH), help="JSON report output path.")
    parser.add_argument("--stale-tolerance-seconds", type=int, default=60)
    parser.add_argument("--check-container-read", action="store_true")
    parser.add_argument("--container-name", default=DEFAULT_CONTAINER_NAME)
    parser.add_argument("--container-db", default=DEFAULT_CONTAINER_DB_PATH)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    log_paths = tuple(Path(item) for item in args.log) if args.log else DEFAULT_LOG_CANDIDATES

    payload = audit_persistence(
        db_path=Path(args.db),
        log_paths=log_paths,
        report_path=Path(args.report),
        stale_tolerance_seconds=int(args.stale_tolerance_seconds),
        check_container_read=bool(args.check_container_read),
        container_name=str(args.container_name),
        container_db_path=str(args.container_db),
    )

    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
