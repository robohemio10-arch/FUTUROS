"""Authoritative read-only closed-paper-trade source resolver.

The adapter reads Freqtrade Paper SQLite in read-only mode and normalizes each
closed trade into the canonical AutoLearning economic contract.  No runtime,
order, risk, model, or SQLite mutation is performed here.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

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
    """Select the freshest valid Paper DB and return closed trades read-only."""

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
        rows = connection.execute(
            f"SELECT * FROM trades WHERE {where} ORDER BY close_date, id"
        ).fetchall()
    return [_freqtrade_row_to_feedback(dict(row)) for row in rows]


def _freqtrade_row_to_feedback(row: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one Freqtrade trade without confusing margin with notional."""

    raw_trade_id = row.get("id")
    trade_id = _identity(raw_trade_id)
    order_id = _identity(row.get("order_id"))
    if not order_id and trade_id:
        # Existing historical AutoLearning outcomes already use this stable ID.
        order_id = f"freqtrade-paper-{trade_id}"

    is_short = _as_bool(row.get("is_short"))
    side = "short" if is_short else "long"
    amount = _finite_float(row.get("amount"))
    open_rate = _finite_float(row.get("open_rate"))
    close_rate = _finite_float(row.get("close_rate"))
    stake_amount = _finite_float(row.get("stake_amount"))
    leverage = _finite_float(row.get("leverage"))

    entry_notional = _notional(amount, open_rate)
    exit_notional = _notional(amount, close_rate)
    notional = entry_notional
    if notional is None:
        open_trade_value = _finite_float(row.get("open_trade_value"))
        if open_trade_value is not None:
            notional = abs(open_trade_value)
    if notional is None and stake_amount is not None:
        notional = abs(stake_amount * leverage) if leverage not in (None, 0.0) else abs(stake_amount)

    gross_pnl = _gross_pnl(
        amount=amount,
        open_rate=open_rate,
        close_rate=close_rate,
        is_short=is_short,
    )
    trading_fee = _trading_fee(
        row=row,
        entry_notional=entry_notional,
        exit_notional=exit_notional,
    )
    funding_fee = _funding_cost(row)

    direct_net_pnl = _first_finite(
        row,
        (
            "close_profit_abs",
            "profit_abs",
            "realized_pnl",
            "net_pnl",
            "pnl",
        ),
    )
    derived_net_pnl: float | None = None
    if gross_pnl is not None and trading_fee is not None and funding_fee is not None:
        derived_net_pnl = gross_pnl - trading_fee - funding_fee

    net_pnl = direct_net_pnl if direct_net_pnl is not None else derived_net_pnl
    close_profit = _finite_float(row.get("close_profit"))
    if net_pnl is None and close_profit is not None and stake_amount is not None:
        net_pnl = close_profit * stake_amount

    return {
        "trade_id": raw_trade_id if raw_trade_id is not None else trade_id,
        "order_id": order_id,
        "pair": row.get("pair"),
        "side": side,
        "open_time": row.get("open_date"),
        "close_time": row.get("close_date"),
        "open_rate": open_rate,
        "close_rate": close_rate,
        "amount": amount,
        "quantity": amount,
        "stake_amount": stake_amount,
        "notional": notional,
        "gross_pnl": gross_pnl,
        "trading_fee": trading_fee,
        "funding_fee": funding_fee,
        "net_pnl": net_pnl,
        "profit_abs": net_pnl,
        "close_profit": close_profit,
        "leverage": leverage,
        "margin_mode": row.get("margin_mode"),
        "liquidation_price": _finite_float(row.get("liquidation_price")),
        "exit_reason": row.get("exit_reason"),
        "strategy": row.get("strategy"),
    }


def _notional(amount: float | None, price: float | None) -> float | None:
    if amount is None or price is None:
        return None
    return abs(amount * price)


def _gross_pnl(
    *,
    amount: float | None,
    open_rate: float | None,
    close_rate: float | None,
    is_short: bool,
) -> float | None:
    if amount is None or open_rate is None or close_rate is None:
        return None
    price_delta = open_rate - close_rate if is_short else close_rate - open_rate
    return amount * price_delta


def _trading_fee(
    *,
    row: Mapping[str, Any],
    entry_notional: float | None,
    exit_notional: float | None,
) -> float | None:
    open_fee = _fee_leg(
        explicit_cost=row.get("fee_open_cost"),
        rate=row.get("fee_open"),
        notional=entry_notional,
    )
    close_fee = _fee_leg(
        explicit_cost=row.get("fee_close_cost"),
        rate=row.get("fee_close"),
        notional=exit_notional,
    )
    known = [fee for fee in (open_fee, close_fee) if fee is not None]
    return sum(known) if known else None


def _fee_leg(
    *,
    explicit_cost: object,
    rate: object,
    notional: float | None,
) -> float | None:
    explicit = _finite_float(explicit_cost)
    if explicit is not None:
        return abs(explicit)
    fee_rate = _finite_float(rate)
    if fee_rate is None or notional is None:
        return None
    return abs(notional * fee_rate)


def _funding_cost(row: Mapping[str, Any]) -> float | None:
    raw = _first_finite(row, ("funding_fees", "funding_fee", "funding"))
    if raw is None:
        return None
    # Freqtrade stores paid funding as a negative value and received funding as
    # positive. Canonical AutoLearning uses positive=cost, negative=credit.
    return -raw


def _first_finite(row: Mapping[str, Any], names: Iterable[str]) -> float | None:
    for name in names:
        value = _finite_float(row.get(name))
        if value is not None:
            return value
    return None


def _finite_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _identity(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() in {"none", "nan", "null"}:
        return ""
    if text.endswith(".0"):
        prefix = text[:-2]
        if prefix.lstrip("+-").isdigit():
            return prefix
    return text


def _as_bool(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


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
