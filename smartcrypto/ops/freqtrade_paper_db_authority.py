from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


DEFAULT_FREQTRADE_PAPER_DB_CANDIDATES = (
    Path("data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite"),
    Path("freqtrade/user_data/tradesv3.paper.sqlite"),
    Path("data/runtime/freqtrade_active_path_snapshot.sqlite"),
    Path("data/evidence/db_persistence_fault_20260601/tradesv3.paper.sqlite"),
)
OPEN_TIME_COLUMNS = (
    "open_date_utc",
    "open_date",
    "open_time_utc",
    "open_time",
    "opened_at",
    "timestamp",
)
CLOSE_TIME_COLUMNS = (
    "close_date_utc",
    "close_date",
    "close_time_utc",
    "close_time",
    "closed_at",
    "exit_time",
)
ACTIVITY_TIME_COLUMNS = (
    *CLOSE_TIME_COLUMNS,
    "updated_at_utc",
    "updated_at",
    "last_update_utc",
    "last_update",
    *OPEN_TIME_COLUMNS,
)


def resolve_freqtrade_paper_db_authority(
    *,
    explicit_path: str | Path | None = None,
    candidate_paths: list[str | Path] | tuple[str | Path, ...] | None = None,
) -> dict[str, Any]:
    """Resolve the authoritative Freqtrade paper SQLite source without writing to it."""

    candidates = unique_paths(
        ([explicit_path] if explicit_path is not None else [])
        + list(candidate_paths or DEFAULT_FREQTRADE_PAPER_DB_CANDIDATES)
    )
    reports = [inspect_freqtrade_paper_db(path) for path in candidates]
    selected: dict[str, Any] | None = None
    reason = "missing_valid_freqtrade_paper_db"

    if explicit_path is not None:
        explicit_report = reports[0] if reports else inspect_freqtrade_paper_db(explicit_path)
        if is_valid_candidate(explicit_report):
            selected = explicit_report
            reason = "explicit_valid_path"
        else:
            reason = f"explicit_invalid:{explicit_report['selection_reason']}"

    if selected is None and explicit_path is None:
        valid = [report for report in reports if is_valid_candidate(report)]
        if valid:
            selected = sorted(
                valid,
                key=lambda item: (
                    int(item.get("total_trades") or 0),
                    parse_utc(item.get("last_activity_date")) or datetime.min.replace(tzinfo=timezone.utc),
                    str(item.get("path") or ""),
                ),
                reverse=True,
            )[0]
            reason = "highest_total_trades_latest_activity"

    stale_candidates: list[str] = []
    for report in reports:
        report["selected"] = selected is not None and report["path"] == selected["path"]
        if report["selected"]:
            report["selection_reason"] = reason
            continue
        if is_valid_candidate(report) and selected is not None:
            report["selection_reason"] = "stale_candidate"
            stale_candidates.append(str(report["path"]))
        elif not report.get("selection_reason"):
            report["selection_reason"] = candidate_rejection_reason(report)

    return {
        "status": "ok" if selected is not None else "blocked",
        "reason": reason if selected is not None else reason,
        "selected_path": selected["path"] if selected else None,
        "selection_reason": reason if selected else None,
        "candidates": reports,
        "stale_candidates": stale_candidates,
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
    }


def inspect_freqtrade_paper_db(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    report: dict[str, Any] = {
        "path": str(target),
        "exists": target.exists(),
        "has_trades_table": False,
        "total_trades": 0,
        "open_trades": 0,
        "closed_trades": 0,
        "first_open_date": None,
        "last_open_date": None,
        "last_activity_date": None,
        "file_mtime": file_mtime_utc(target),
        "selected": False,
        "selection_reason": "missing_file" if not target.exists() else None,
    }
    if not target.exists():
        return report

    try:
        with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            has_table = bool(
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'trades'"
                ).fetchone()
            )
            report["has_trades_table"] = has_table
            if not has_table:
                report["selection_reason"] = "missing_trades_table"
                return report
            rows = [dict(row) for row in connection.execute('SELECT * FROM "trades"').fetchall()]
    except sqlite3.Error as exc:
        report["selection_reason"] = f"sqlite_read_failed:{exc}"
        return report

    report["total_trades"] = len(rows)
    if not rows:
        report["selection_reason"] = "empty_trades_table"
        return report

    open_count = sum(1 for row in rows if row_is_open(row))
    open_times = [first_valid_time(row, OPEN_TIME_COLUMNS) for row in rows]
    activity_times = [first_valid_time(row, ACTIVITY_TIME_COLUMNS) for row in rows]
    valid_open_times = [value for value in open_times if value is not None]
    valid_activity_times = [value for value in activity_times if value is not None]
    report.update(
        {
            "open_trades": open_count,
            "closed_trades": len(rows) - open_count,
            "first_open_date": iso(min(valid_open_times)) if valid_open_times else None,
            "last_open_date": iso(max(valid_open_times)) if valid_open_times else None,
            "last_activity_date": iso(max(valid_activity_times)) if valid_activity_times else None,
            "selection_reason": None,
        }
    )
    return report


def unique_paths(paths: Sequence[str | Path | None]) -> list[Path]:
    seen: set[str] = set()
    unique: list[Path] = []
    for raw in paths:
        if raw is None:
            continue
        path = Path(raw)
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def is_valid_candidate(report: dict[str, Any]) -> bool:
    return (
        bool(report.get("exists"))
        and bool(report.get("has_trades_table"))
        and int(report.get("total_trades") or 0) > 0
    )


def candidate_rejection_reason(report: dict[str, Any]) -> str:
    if not report.get("exists"):
        return "missing_file"
    if not report.get("has_trades_table"):
        return "missing_trades_table"
    if int(report.get("total_trades") or 0) <= 0:
        return "empty_trades_table"
    return "not_selected"


def row_is_open(row: dict[str, Any]) -> bool:
    if "is_open" in row and row["is_open"] is not None:
        return str(row["is_open"]).strip().lower() in {"1", "true", "yes", "open"}
    close_time = first_valid_time(row, CLOSE_TIME_COLUMNS)
    return close_time is None


def first_valid_time(row: dict[str, Any], columns: tuple[str, ...]) -> datetime | None:
    for column in columns:
        if column not in row:
            continue
        parsed = parse_utc(row.get(column))
        if parsed is not None:
            return parsed
    return None


def parse_utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def file_mtime_utc(path: Path) -> str | None:
    if not path.exists():
        return None
    return iso(datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc))
