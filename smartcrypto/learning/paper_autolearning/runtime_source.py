"""Authoritative read-only closed-paper-trade source resolver.

This module deliberately opens Freqtrade SQLite databases in read-only mode and
selects the freshest valid Paper source by closed-trade time.  It never writes
the database, changes runtime state, submits orders, or grants model authority.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

PAPER_DB_CANDIDATES: tuple[Path, ...] = (
    Path("freqtrade/user_data/tradesv3.paper.sqlite"),
    Path("data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite"),
    Path("data/runtime/freqtrade_active_path_snapshot.sqlite"),
    Path("user_data/tradesv3.paper.sqlite"),
    Path("data/runtime/freqtrade/tradesv3.paper.sqlite"),
    Path("data/freqtrade/tradesv3.paper.sqlite"),
)


@dataclass(frozen=True)
class PaperSourceCandidate:
    path: Path
    status: str
    closed_trade_count: int
    max_close_time_utc: str | None
    mtime_utc: str | None
    reason: str | None = None


@dataclass(frozen=True)
class PaperSourceSelection:
    status: str
    reason: str
    selected_path: Path | None
    rows: tuple[dict[str, Any], ...]
    candidates: tuple[PaperSourceCandidate, ...]


def load_authoritative_closed_paper_trades(
    *,
    project_root: str | Path,
    explicit_path: str | Path | None = None,
) -> PaperSourceSelection:
    """Select the freshest valid Paper DB and return its closed trades read-only."""

    root = Path(project_root).resolve()
    candidate_paths = _candidate_paths(root, explicit_path)
    inspected = tuple(_inspect_candidate(path) for path in candidate_paths)
    valid = [item for item in inspected if item.status == "ok" and item.closed_trade_count > 0]
    if not valid:
        return PaperSourceSelection(
            status="blocked",
            reason="no_valid_closed_paper_trade_source",
            selected_path=None,
            rows=(),
            candidates=inspected,
        )

    selected = max(valid, key=_freshness_key)
    rows = tuple(_read_closed_trades(selected.path))
    if not rows:
        return PaperSourceSelection(
            status="blocked",
            reason="selected_source_returned_no_closed_trades",
            selected_path=selected.path,
            rows=(),
            candidates=inspected,
        )
    return PaperSourceSelection(
        status="ok",
        reason="freshest_closed_trade_source_selected_read_only",
        selected_path=selected.path,
        rows=rows,
        candidates=inspected,
    )


def _candidate_paths(root: Path, explicit_path: str | Path | None) -> tuple[Path, ...]:
    ordered: list[Path] = []
    if explicit_path is not None:
        explicit = Path(explicit_path)
        ordered.append(explicit if explicit.is_absolute() else root / explicit)
    ordered.extend(root / relative for relative in PAPER_DB_CANDIDATES)
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in ordered:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return tuple(unique)


def _inspect_candidate(path: Path) -> PaperSourceCandidate:
    if not path.exists() or not path.is_file():
        return PaperSourceCandidate(path, "missing", 0, None, None, "file_not_found")
    try:
        stat = path.stat()
        with _connect_read_only(path) as connection:
            columns = _table_columns(connection, "trades")
            required = {"id", "pair", "close_date"}
            if not required.issubset(columns):
                return PaperSourceCandidate(
                    path,
                    "invalid_schema",
                    0,
                    None,
                    _iso_from_timestamp(stat.st_mtime),
                    "missing_required_trade_columns",
                )
            where = _closed_where(columns)
            row = connection.execute(
                f"SELECT COUNT(*) AS n, MAX(close_date) AS max_close FROM trades WHERE {where}"
            ).fetchone()
            count = int(row[0] or 0)
            max_close = _normalize_utc_text(row[1])
        return PaperSourceCandidate(
            path,
            "ok",
            count,
            max_close,
            _iso_from_timestamp(stat.st_mtime),
            None,
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        return PaperSourceCandidate(path, "unreadable", 0, None, None, type(exc).__name__)


def _read_closed_trades(path: Path) -> list[dict[str, Any]]:
    with _connect_read_only(path) as connection:
        connection.row_factory = sqlite3.Row
        columns = _table_columns(connection, "trades")
        where = _closed_where(columns)
        rows = connection.execute(f"SELECT * FROM trades WHERE {where} ORDER BY close_date, id").fetchall()
    return [_freqtrade_row_to_feedback(dict(row)) for row in rows]


def _freqtrade_row_to_feedback(row: dict[str, Any]) -> dict[str, Any]:
    is_short = bool(row.get("is_short"))
    return {
        "trade_id": row.get("id"),
        "order_id": row.get("order_id"),
        "pair": row.get("pair"),
        "side": "short" if is_short else "long",
        "open_time": row.get("open_date"),
        "close_time": row.get("close_date"),
        "open_rate": row.get("open_rate"),
        "close_rate": row.get("close_rate"),
        "amount": row.get("amount"),
        "stake_amount": row.get("stake_amount"),
        "profit_abs": row.get("profit_abs"),
        "close_profit": row.get("close_profit"),
        "leverage": row.get("leverage"),
        "liquidation_price": row.get("liquidation_price"),
        "exit_reason": row.get("exit_reason"),
        "strategy": row.get("strategy"),
    }


def _connect_read_only(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}


def _closed_where(columns: Iterable[str]) -> str:
    column_set = set(columns)
    conditions = ["close_date IS NOT NULL"]
    if "is_open" in column_set:
        conditions.append("is_open = 0")
    return " AND ".join(conditions)


def _freshness_key(candidate: PaperSourceCandidate) -> tuple[datetime, datetime, int]:
    return (
        _parse_datetime(candidate.max_close_time_utc),
        _parse_datetime(candidate.mtime_utc),
        candidate.closed_trade_count,
    )


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=UTC)
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _normalize_utc_text(value: Any) -> str | None:
    if value is None:
        return None
    parsed = _parse_datetime(str(value))
    if parsed == datetime.min.replace(tzinfo=UTC):
        return None
    return parsed.isoformat()


def _iso_from_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, tz=UTC).isoformat()
