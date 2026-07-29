"""Offline-first, point-in-time candle recovery with explicit terminal gaps."""

from __future__ import annotations

import hashlib
import re
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pandas as pd

from .contracts import json_safe, stable_hash

CANDLE_RECOVERY_SCHEMA_VERSION = "canonical_candle_recovery_v2"
CANDLE_TERMINAL_STATUSES = frozenset({"RECOVERED_VERIFIED", "PERMANENT_QUARANTINE"})
ALLOWED_PUBLIC_HOSTS = frozenset({"fapi.binance.com", "www.bitradex.ai"})
ALLOWED_PUBLIC_QUERY_KEYS = frozenset(
    {"symbol", "interval", "startTime", "endTime", "limit"}
)

HttpTransport = Callable[[str, float, Mapping[str, str]], bytes]
Sleep = Callable[[float], None]


@dataclass(frozen=True)
class PublicCandleRequestPolicy:
    timeout_seconds: float = 10.0
    max_attempts: int = 3
    backoff_seconds: float = 0.5
    minimum_request_interval_seconds: float = 0.1
    user_agent: str = "SMART-FUTUROS-canonical-data-foundation-v2/1.0"


@dataclass(frozen=True)
class CandleSourceSpec:
    source_id: str
    source_type: str
    timeframe: str
    paths: tuple[str, ...]
    public_endpoint: str
    priority: int
    public_read_only: bool = True


@dataclass(frozen=True)
class CandleRecoveryRecord:
    source_trade_reference: str
    symbol: str
    open_time_utc: str | None
    close_time_utc: str | None
    terminal_status: str
    terminal_reason_codes: tuple[str, ...]
    selected_source_id: str | None
    selected_source_hashes: tuple[str, ...]
    source_attempts: tuple[Mapping[str, Any], ...]
    entry_feature_timestamp_utc: str | None
    exit_available_timestamp_utc: str | None
    gap_detected: bool
    gap_start_utc: str | None
    gap_end_utc: str | None
    missing_interval_count: int
    forward_fill_used: bool
    point_in_time_valid: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_trade_reference": self.source_trade_reference,
            "symbol": self.symbol,
            "open_time_utc": self.open_time_utc,
            "close_time_utc": self.close_time_utc,
            "terminal_status": self.terminal_status,
            "terminal_reason_codes": list(self.terminal_reason_codes),
            "selected_source_id": self.selected_source_id,
            "selected_source_hashes": list(self.selected_source_hashes),
            "source_attempts": [dict(item) for item in self.source_attempts],
            "entry_feature_timestamp_utc": self.entry_feature_timestamp_utc,
            "exit_available_timestamp_utc": self.exit_available_timestamp_utc,
            "gap_detected": self.gap_detected,
            "gap_start_utc": self.gap_start_utc,
            "gap_end_utc": self.gap_end_utc,
            "missing_interval_count": self.missing_interval_count,
            "forward_fill_used": self.forward_fill_used,
            "point_in_time_valid": self.point_in_time_valid,
        }


@dataclass(frozen=True)
class CandleRecoveryResult:
    report: Mapping[str, Any]
    records: tuple[CandleRecoveryRecord, ...]


@dataclass(frozen=True)
class _LoadedCandleSource:
    spec: CandleSourceSpec
    frame: pd.DataFrame
    timestamps_by_symbol: Mapping[str, frozenset[pd.Timestamp]]
    audits: tuple[Mapping[str, Any], ...]
    structural_errors: tuple[str, ...]


def fetch_public_candle_payload(
    *,
    url: str,
    transport: HttpTransport,
    policy: PublicCandleRequestPolicy = PublicCandleRequestPolicy(),
    sleep: Sleep = time.sleep,
) -> dict[str, Any]:
    """Fetch public bytes through an injected transport with bounded retries."""

    sanitized_url = sanitize_public_candle_url(url)
    if policy.timeout_seconds <= 0 or policy.max_attempts < 1:
        raise ValueError("invalid_public_candle_request_policy")
    errors: list[str] = []
    previous_request_at: float | None = None
    for attempt in range(1, policy.max_attempts + 1):
        now = time.monotonic()
        if previous_request_at is not None:
            remaining = policy.minimum_request_interval_seconds - (now - previous_request_at)
            if remaining > 0:
                sleep(remaining)
        previous_request_at = time.monotonic()
        try:
            payload = transport(
                url,
                policy.timeout_seconds,
                {"User-Agent": policy.user_agent},
            )
            if not payload:
                raise ValueError("empty_public_candle_response")
            return {
                "status": "ok",
                "attempt_count": attempt,
                "response_sha256": hashlib.sha256(payload).hexdigest(),
                "response_bytes": payload,
                "source_url_sanitized": sanitized_url,
            }
        except (OSError, TimeoutError, ValueError) as exc:
            errors.append(type(exc).__name__)
            if attempt < policy.max_attempts:
                sleep(policy.backoff_seconds * attempt)
    return {
        "status": "blocked",
        "reason": "public_candle_fetch_failed",
        "attempt_count": policy.max_attempts,
        "errors": errors,
        "response_sha256": None,
        "response_bytes": None,
        "source_url_sanitized": sanitized_url,
    }


def sanitize_public_candle_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_PUBLIC_HOSTS:
        raise ValueError("public_candle_url_not_allowlisted")
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=False)
        if key in ALLOWED_PUBLIC_QUERY_KEYS
    ]
    return urlunsplit(
        (
            "https",
            parsed.hostname,
            parsed.path,
            urlencode(query),
            "",
        )
    )


def recover_blocked_candles(
    *,
    project_root: str | Path,
    blocked_trades: pd.DataFrame | Sequence[Mapping[str, Any]],
    primary_sources: Sequence[CandleSourceSpec],
    secondary_sources: Sequence[CandleSourceSpec],
    divergence_tolerance: float = 1e-8,
) -> CandleRecoveryResult:
    """Recover blocked rows from immutable public archives, without filling gaps."""

    root = Path(project_root).resolve()
    trades = _normalize_trades(blocked_trades)
    primary = tuple(_load_source(root, spec) for spec in sorted(primary_sources, key=_priority))
    secondary = tuple(
        _load_source(root, spec) for spec in sorted(secondary_sources, key=_priority)
    )
    records: list[CandleRecoveryRecord] = []
    for row in trades.to_dict(orient="records"):
        records.append(
            _recover_one(
                row=row,
                primary=primary,
                secondary=secondary,
                divergence_tolerance=divergence_tolerance,
            )
        )
    terminal = Counter(record.terminal_status for record in records)
    reasons: Counter[str] = Counter()
    for record in records:
        reasons.update(record.terminal_reason_codes)
    all_terminal = len(records) == sum(
        terminal[status] for status in CANDLE_TERMINAL_STATUSES
    )
    report = {
        "schema_version": CANDLE_RECOVERY_SCHEMA_VERSION,
        "status": "ok" if all_terminal else "blocked",
        "reason": (
            "all_blocked_candle_rows_terminally_classified"
            if all_terminal
            else "candle_terminal_classification_incomplete"
        ),
        "candle_blocked_input_rows": len(records),
        "candle_recovered_verified_rows": terminal["RECOVERED_VERIFIED"],
        "candle_permanent_quarantine_rows": terminal["PERMANENT_QUARANTINE"],
        "candle_unresolved_rows": len(records)
        - terminal["RECOVERED_VERIFIED"]
        - terminal["PERMANENT_QUARANTINE"],
        "forward_fill_used": False,
        "gaps_preserved": all(
            (not record.gap_detected) or record.missing_interval_count > 0
            for record in records
        ),
        "source_attempts_exhausted_for_quarantine": all(
            record.source_attempts
            for record in records
            if record.terminal_status == "PERMANENT_QUARANTINE"
        ),
        "quarantine_reason_counts": dict(sorted(reasons.items())),
        "primary_source_inventory": _source_inventory(primary),
        "secondary_source_inventory": _source_inventory(secondary),
        "record_set_hash": stable_hash([record.to_dict() for record in records]),
        "record_sample": [record.to_dict() for record in records[:3]],
        "public_market_data_only": True,
        "exchange_private_access": False,
        "write_performed": False,
        "writes_candles": False,
    }
    return CandleRecoveryResult(report=report, records=tuple(records))


def _recover_one(
    *,
    row: Mapping[str, Any],
    primary: Sequence[_LoadedCandleSource],
    secondary: Sequence[_LoadedCandleSource],
    divergence_tolerance: float,
) -> CandleRecoveryRecord:
    symbol = _symbol(row.get("symbol"))
    opened = pd.to_datetime(row.get("open_time"), utc=True, errors="coerce")
    closed = pd.to_datetime(row.get("close_time"), utc=True, errors="coerce")
    reference = _trade_reference(row)
    if pd.isna(opened) or pd.isna(closed) or opened > closed:
        invalid_attempts = tuple(
            {
                "source_id": source.spec.source_id,
                "source_type": source.spec.source_type,
                "status": "not_queried",
                "reason": "invalid_trade_interval",
                "source_hashes": [audit.get("sha256") for audit in source.audits],
            }
            for source in (*primary, *secondary)
        )
        return _quarantine(
            reference=reference,
            symbol=symbol,
            opened=opened,
            closed=closed,
            reasons=("invalid_or_missing_trade_interval",),
            attempts=invalid_attempts,
        )

    attempts: list[Mapping[str, Any]] = []
    selected: _LoadedCandleSource | None = None
    selected_coverage: Mapping[str, Any] | None = None
    for source in primary:
        coverage = _coverage(source, symbol=symbol, opened=opened, closed=closed)
        attempts.append(_attempt(source, coverage))
        if coverage["status"] == "ok":
            selected = source
            selected_coverage = coverage
            break
    if selected is None:
        for source in secondary:
            coverage = _coverage(source, symbol=symbol, opened=opened, closed=closed)
            attempts.append(_attempt(source, coverage))
            if coverage["status"] == "ok":
                selected = source
                selected_coverage = coverage
                break
    else:
        assert selected_coverage is not None
        comparable = [
            source
            for source in secondary
            if source.spec.timeframe == selected.spec.timeframe
        ]
        for source in comparable:
            coverage = _coverage(source, symbol=symbol, opened=opened, closed=closed)
            attempts.append(_attempt(source, coverage))
            if coverage["status"] != "ok":
                continue
            divergence = _source_divergence(
                selected,
                source,
                symbol=symbol,
                start=pd.Timestamp(selected_coverage["entry_feature_timestamp_utc"]),
                end=pd.Timestamp(selected_coverage["exit_available_timestamp_utc"]),
                tolerance=divergence_tolerance,
            )
            if divergence:
                return _quarantine(
                    reference=reference,
                    symbol=symbol,
                    opened=opened,
                    closed=closed,
                    reasons=("primary_secondary_source_divergence",),
                    attempts=tuple(attempts),
                )

    if selected is None or selected_coverage is None:
        gap_attempts = [
            attempt
            for attempt in attempts
            if _integer(attempt.get("missing_interval_count")) > 0
        ]
        gap_count = sum(
            _integer(attempt.get("missing_interval_count"))
            for attempt in gap_attempts
        )
        gap_start = next(
            (
                str(attempt.get("gap_start_utc"))
                for attempt in gap_attempts
                if attempt.get("gap_start_utc")
            ),
            None,
        )
        gap_end = next(
            (
                str(attempt.get("gap_end_utc"))
                for attempt in reversed(gap_attempts)
                if attempt.get("gap_end_utc")
            ),
            None,
        )
        return _quarantine(
            reference=reference,
            symbol=symbol,
            opened=opened,
            closed=closed,
            reasons=("primary_and_secondary_candle_evidence_exhausted",),
            attempts=tuple(attempts),
            gap_count=gap_count,
            gap_start=gap_start,
            gap_end=gap_end,
        )

    source_hashes = tuple(
        str(audit["sha256"]) for audit in selected.audits if audit.get("sha256")
    )
    return CandleRecoveryRecord(
        source_trade_reference=reference,
        symbol=symbol,
        open_time_utc=opened.isoformat(),
        close_time_utc=closed.isoformat(),
        terminal_status="RECOVERED_VERIFIED",
        terminal_reason_codes=("authoritative_candle_archive_coverage_verified",),
        selected_source_id=selected.spec.source_id,
        selected_source_hashes=source_hashes,
        source_attempts=tuple(attempts),
        entry_feature_timestamp_utc=str(
            selected_coverage["entry_feature_timestamp_utc"]
        ),
        exit_available_timestamp_utc=str(
            selected_coverage["exit_available_timestamp_utc"]
        ),
        gap_detected=False,
        gap_start_utc=None,
        gap_end_utc=None,
        missing_interval_count=0,
        forward_fill_used=False,
        point_in_time_valid=True,
    )


def _coverage(
    source: _LoadedCandleSource,
    *,
    symbol: str,
    opened: pd.Timestamp,
    closed: pd.Timestamp,
) -> dict[str, Any]:
    if source.structural_errors:
        return {
            "status": "blocked",
            "reason": "source_structural_validation_failed",
            "structural_errors": list(source.structural_errors),
        }
    timestamps = source.timestamps_by_symbol.get(symbol, frozenset())
    if not timestamps:
        return {"status": "blocked", "reason": "symbol_not_available"}
    delta = pd.Timedelta(source.spec.timeframe)
    expected_entry = opened.floor(delta) - delta
    expected_exit = closed.floor(delta) - delta
    if expected_exit < expected_entry:
        expected_exit = expected_entry
    if expected_entry not in timestamps:
        return {"status": "blocked", "reason": "entry_candle_not_available_point_in_time"}
    if expected_exit not in timestamps:
        return {"status": "blocked", "reason": "exit_candle_not_available"}
    entry = expected_entry
    exit_timestamp = expected_exit
    if entry > exit_timestamp:
        return {"status": "blocked", "reason": "invalid_available_candle_interval"}
    expected = pd.date_range(entry, exit_timestamp, freq=delta, tz="UTC")
    missing = [timestamp for timestamp in expected if timestamp not in timestamps]
    if missing:
        return {
            "status": "blocked",
            "reason": "candle_gap_detected",
            "gap_detected": True,
            "gap_start_utc": missing[0].isoformat(),
            "gap_end_utc": missing[-1].isoformat(),
            "missing_interval_count": len(missing),
        }
    return {
        "status": "ok",
        "reason": "point_in_time_coverage_complete",
        "entry_feature_timestamp_utc": entry.isoformat(),
        "exit_available_timestamp_utc": exit_timestamp.isoformat(),
        "missing_interval_count": 0,
    }


def _load_source(root: Path, spec: CandleSourceSpec) -> _LoadedCandleSource:
    frames: list[pd.DataFrame] = []
    audits: list[Mapping[str, Any]] = []
    errors: list[str] = []
    if not spec.public_read_only:
        errors.append("source_not_public_read_only")
    try:
        endpoint = sanitize_public_candle_url(spec.public_endpoint)
    except ValueError as exc:
        endpoint = None
        errors.append(str(exc))
    for value in spec.paths:
        requested = Path(value)
        path = requested if requested.is_absolute() else root / requested
        display = _display(path, root)
        if not _safe_source_path(path, root):
            audits.append(
                {
                    "path": display,
                    "status": "missing_or_unsafe",
                    "sha256": None,
                    "source_url_sanitized": endpoint,
                }
            )
            continue
        source_hash = _file_sha256(path)
        try:
            raw = (
                pd.read_parquet(path)
                if path.suffix.lower() == ".parquet"
                else pd.read_csv(path)
            )
            normalized, validation = _normalize_candles(raw, spec=spec)
            frames.append(normalized)
            audits.append(
                {
                    "path": display,
                    "status": "inspected",
                    "sha256": source_hash,
                    "row_count": len(normalized),
                    "source_url_sanitized": endpoint,
                    **validation,
                }
            )
            errors.extend(validation["validation_errors"])
        except (OSError, ValueError, TypeError) as exc:
            errors.append(f"source_read_failed:{type(exc).__name__}")
            audits.append(
                {
                    "path": display,
                    "status": "unreadable",
                    "sha256": source_hash,
                    "source_url_sanitized": endpoint,
                }
            )
    if frames:
        frame = pd.concat(frames, ignore_index=True)
        frame = frame.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    else:
        frame = pd.DataFrame(
            columns=["symbol", "timestamp", "open", "high", "low", "close", "volume"]
        )
    if not frames:
        errors.append("source_archive_unavailable")
    timestamp_index = {
        str(symbol): frozenset(group["timestamp"])
        for symbol, group in frame.groupby("symbol", sort=False)
    }
    return _LoadedCandleSource(
        spec=spec,
        frame=frame,
        timestamps_by_symbol=timestamp_index,
        audits=tuple(audits),
        structural_errors=tuple(sorted(set(errors))),
    )


def _normalize_candles(
    raw: pd.DataFrame,
    *,
    spec: CandleSourceSpec,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    aliases = {str(column).lower(): str(column) for column in raw.columns}
    ts_col = _first_alias(aliases, ("timestamp", "ts", "open_time", "open_time_utc"))
    symbol_col = _first_alias(aliases, ("symbol", "pair"))
    required = {
        "open": _first_alias(aliases, ("open", "o")),
        "high": _first_alias(aliases, ("high", "h")),
        "low": _first_alias(aliases, ("low", "l")),
        "close": _first_alias(aliases, ("close", "c")),
        "volume": _first_alias(aliases, ("volume", "v")),
    }
    missing = [name for name, column in required.items() if column is None]
    if ts_col is None or symbol_col is None or missing:
        raise ValueError("candle_source_schema_missing_required_columns")
    timezone_explicit = _timezone_is_explicit(raw[ts_col])
    timestamp = pd.to_datetime(raw[ts_col], utc=True, errors="coerce")
    frame = pd.DataFrame(
        {
            "symbol": raw[symbol_col].map(_symbol),
            "timestamp": timestamp,
            **{
                name: pd.to_numeric(raw[column], errors="coerce")
                for name, column in required.items()
                if column is not None
            },
        }
    )
    errors: list[str] = []
    if not timezone_explicit:
        errors.append("source_timezone_ambiguous")
    if frame["timestamp"].isna().any():
        errors.append("invalid_candle_timestamp")
    if frame.duplicated(["symbol", "timestamp"]).any():
        errors.append("duplicate_candle_timestamp")
    for _, group in frame.groupby("symbol", sort=False):
        if not group["timestamp"].is_monotonic_increasing:
            errors.append("candle_timestamp_not_monotonic")
            break
    numeric = frame[["open", "high", "low", "close", "volume"]]
    if numeric.isna().any().any():
        errors.append("invalid_candle_numeric_value")
    invalid_ohlc = (
        (frame["high"] < frame[["open", "close"]].max(axis=1))
        | (frame["low"] > frame[["open", "close"]].min(axis=1))
        | (frame["high"] < frame["low"])
        | (frame["volume"] < 0)
    )
    if invalid_ohlc.any():
        errors.append("invalid_candle_ohlc")
    is_closed_col = _first_alias(aliases, ("is_closed", "closed", "is_final"))
    if is_closed_col is not None and not raw[is_closed_col].fillna(False).astype(bool).all():
        errors.append("incomplete_candle_present")
    return frame, {
        "timeframe": spec.timeframe,
        "timezone_status": "explicit_utc_or_offset" if timezone_explicit else "ambiguous",
        "validation_errors": sorted(set(errors)),
    }


def _source_divergence(
    primary: _LoadedCandleSource,
    secondary: _LoadedCandleSource,
    *,
    symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    tolerance: float,
) -> bool:
    columns = ["timestamp", "open", "high", "low", "close", "volume"]
    left = primary.frame[
        primary.frame["symbol"].eq(symbol)
        & primary.frame["timestamp"].between(start, end)
    ][columns]
    right = secondary.frame[
        secondary.frame["symbol"].eq(symbol)
        & secondary.frame["timestamp"].between(start, end)
    ][columns]
    merged = left.merge(right, on="timestamp", suffixes=("_primary", "_secondary"))
    if merged.empty:
        return False
    for column in ("open", "high", "low", "close"):
        left_value = merged[f"{column}_primary"]
        right_value = merged[f"{column}_secondary"]
        allowed = tolerance * left_value.abs().clip(lower=1.0)
        if ((left_value - right_value).abs() > allowed).any():
            return True
    return False


def _attempt(
    source: _LoadedCandleSource,
    coverage: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "source_id": source.spec.source_id,
        "source_type": source.spec.source_type,
        "timeframe": source.spec.timeframe,
        "status": coverage.get("status"),
        "reason": coverage.get("reason"),
        "source_hashes": [
            audit.get("sha256") for audit in source.audits if audit.get("sha256")
        ],
        "source_urls_sanitized": [
            audit.get("source_url_sanitized")
            for audit in source.audits
            if audit.get("source_url_sanitized")
        ],
        "structural_errors": list(coverage.get("structural_errors", ())),
        "gap_detected": bool(coverage.get("gap_detected", False)),
        "gap_start_utc": coverage.get("gap_start_utc"),
        "gap_end_utc": coverage.get("gap_end_utc"),
        "missing_interval_count": int(coverage.get("missing_interval_count") or 0),
    }


def _quarantine(
    *,
    reference: str,
    symbol: str,
    opened: Any,
    closed: Any,
    reasons: Sequence[str],
    attempts: Sequence[Mapping[str, Any]],
    gap_count: int = 0,
    gap_start: str | None = None,
    gap_end: str | None = None,
) -> CandleRecoveryRecord:
    return CandleRecoveryRecord(
        source_trade_reference=reference,
        symbol=symbol,
        open_time_utc=None if pd.isna(opened) else opened.isoformat(),
        close_time_utc=None if pd.isna(closed) else closed.isoformat(),
        terminal_status="PERMANENT_QUARANTINE",
        terminal_reason_codes=tuple(
            sorted({*reasons, "public_candle_sources_terminally_exhausted"})
        ),
        selected_source_id=None,
        selected_source_hashes=(),
        source_attempts=tuple(attempts),
        entry_feature_timestamp_utc=None,
        exit_available_timestamp_utc=None,
        gap_detected=gap_count > 0,
        gap_start_utc=gap_start,
        gap_end_utc=gap_end,
        missing_interval_count=gap_count,
        forward_fill_used=False,
        point_in_time_valid=False,
    )


def _normalize_trades(
    rows: pd.DataFrame | Sequence[Mapping[str, Any]],
) -> pd.DataFrame:
    frame = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    aliases = {str(column).lower(): str(column) for column in frame.columns}
    symbol = _first_alias(aliases, ("symbol", "moeda"))
    opened = _first_alias(aliases, ("open_time", "open_time_utc", "horario_abertura"))
    closed = _first_alias(
        aliases, ("close_time", "close_time_utc", "horario_fechamento")
    )
    if symbol is None:
        frame["symbol"] = "UNKNOWN"
    elif symbol != "symbol":
        frame["symbol"] = frame[symbol]
    if opened is None:
        frame["open_time"] = None
    elif opened != "open_time":
        frame["open_time"] = frame[opened]
    if closed is None:
        frame["close_time"] = None
    elif closed != "close_time":
        frame["close_time"] = frame[closed]
    return frame


def _trade_reference(row: Mapping[str, Any]) -> str:
    existing = row.get("source_record_reference") or row.get("trade_id")
    if existing is not None and str(existing).strip():
        return str(existing)
    return stable_hash(
        {
            "schema_version": CANDLE_RECOVERY_SCHEMA_VERSION,
            "symbol": _symbol(row.get("symbol")),
            "open_time": json_safe(row.get("open_time")),
            "close_time": json_safe(row.get("close_time")),
        }
    )


def _source_inventory(
    sources: Sequence[_LoadedCandleSource],
) -> list[dict[str, Any]]:
    return [
        {
            "source_id": source.spec.source_id,
            "source_type": source.spec.source_type,
            "timeframe": source.spec.timeframe,
            "priority": source.spec.priority,
            "public_read_only": source.spec.public_read_only,
            "structural_errors": list(source.structural_errors),
            "artifacts": [dict(item) for item in source.audits],
        }
        for source in sources
    ]


def _timezone_is_explicit(series: pd.Series) -> bool:
    if isinstance(series.dtype, pd.DatetimeTZDtype):
        return True
    non_null = series.dropna()
    if non_null.empty:
        return False
    sample = non_null.astype(str).head(100)
    pattern = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})$")
    return bool(sample.map(lambda value: bool(pattern.search(value.strip()))).all())


def _symbol(value: Any) -> str:
    text = str(value or "").upper().strip()
    return text.replace("/", "").replace("_", "").replace(":USDT", "")


def _first_alias(
    aliases: Mapping[str, str],
    candidates: Sequence[str],
) -> str | None:
    return next((aliases[name] for name in candidates if name in aliases), None)


def _priority(source: CandleSourceSpec) -> tuple[int, str]:
    return source.priority, source.source_id


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_source_path(path: Path, root: Path) -> bool:
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
        return resolved.is_file() and not path.is_symlink()
    except (FileNotFoundError, OSError, ValueError):
        return False


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _display(path: Path, root: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(root).as_posix()
    except ValueError:
        return path.resolve(strict=False).as_posix()
