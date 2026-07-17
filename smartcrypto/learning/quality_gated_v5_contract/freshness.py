"""Temporal and freshness gates for model feature snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from .contracts import (
    FRESHNESS_MAX_AGE_SECONDS,
    SNAPSHOT_TIMESTAMP_SEMANTICS,
    TIMEFRAME_SECONDS,
)


@dataclass(frozen=True)
class FreshnessResult:
    timeframe: str
    trade_open_time: pd.Timestamp | None
    snapshot_time: pd.Timestamp | None
    snapshot_available_at: pd.Timestamp | None
    snapshot_age_seconds: float | None
    snapshot_is_missing: bool
    snapshot_is_future: bool
    snapshot_is_in_progress: bool
    snapshot_is_stale: bool
    freshness_threshold_seconds: int
    timestamp_semantics: str
    status: str
    block_reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        prefix = f"snapshot_{self.timeframe}"
        return {
            f"{prefix}_time": iso(self.snapshot_time),
            f"{prefix}_available_at": iso(self.snapshot_available_at),
            f"{prefix}_age_seconds": self.snapshot_age_seconds,
            f"{prefix}_is_missing": self.snapshot_is_missing,
            f"{prefix}_is_future": self.snapshot_is_future,
            f"{prefix}_is_in_progress": self.snapshot_is_in_progress,
            f"{prefix}_is_stale": self.snapshot_is_stale,
            f"{prefix}_freshness_threshold_seconds": self.freshness_threshold_seconds,
            f"{prefix}_timestamp_semantics": self.timestamp_semantics,
            f"{prefix}_status": self.status,
            f"{prefix}_block_reasons": list(self.block_reasons),
        }


def to_utc_timestamp(value: Any) -> pd.Timestamp | None:
    if value is None:
        return None
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(parsed):
        return None
    return pd.Timestamp(parsed)


def iso(value: pd.Timestamp | None) -> str | None:
    return value.isoformat() if value is not None else None


def evaluate_snapshot_freshness(
    *,
    trade_open_time: Any,
    snapshot_time: Any,
    timeframe: str,
    max_age_seconds: int | None = None,
    timestamp_semantics: str = SNAPSHOT_TIMESTAMP_SEMANTICS,
) -> FreshnessResult:
    if timeframe not in TIMEFRAME_SECONDS:
        raise ValueError(f"unsupported_timeframe:{timeframe}")

    threshold = int(
        FRESHNESS_MAX_AGE_SECONDS[timeframe]
        if max_age_seconds is None
        else max_age_seconds
    )
    if threshold < 0:
        raise ValueError("freshness_threshold_must_be_nonnegative")

    open_ts = to_utc_timestamp(trade_open_time)
    snap_ts = to_utc_timestamp(snapshot_time)
    reasons: list[str] = []

    if timestamp_semantics != "candle_open":
        reasons.append("BLOCKED_UNKNOWN_SNAPSHOT_TIMESTAMP_SEMANTICS")
        return FreshnessResult(
            timeframe=timeframe,
            trade_open_time=open_ts,
            snapshot_time=snap_ts,
            snapshot_available_at=None,
            snapshot_age_seconds=None,
            snapshot_is_missing=snap_ts is None,
            snapshot_is_future=False,
            snapshot_is_in_progress=False,
            snapshot_is_stale=False,
            freshness_threshold_seconds=threshold,
            timestamp_semantics=timestamp_semantics,
            status="blocked",
            block_reasons=tuple(reasons),
        )

    if open_ts is None:
        return FreshnessResult(
            timeframe=timeframe,
            trade_open_time=None,
            snapshot_time=snap_ts,
            snapshot_available_at=None,
            snapshot_age_seconds=None,
            snapshot_is_missing=snap_ts is None,
            snapshot_is_future=False,
            snapshot_is_in_progress=False,
            snapshot_is_stale=False,
            freshness_threshold_seconds=threshold,
            timestamp_semantics=timestamp_semantics,
            status="blocked",
            block_reasons=("BLOCKED_INVALID_OPEN_TIME",),
        )

    if snap_ts is None:
        return FreshnessResult(
            timeframe=timeframe,
            trade_open_time=open_ts,
            snapshot_time=None,
            snapshot_available_at=None,
            snapshot_age_seconds=None,
            snapshot_is_missing=True,
            snapshot_is_future=False,
            snapshot_is_in_progress=False,
            snapshot_is_stale=False,
            freshness_threshold_seconds=threshold,
            timestamp_semantics=timestamp_semantics,
            status="blocked",
            block_reasons=(f"BLOCKED_MISSING_{timeframe.upper()}_SNAPSHOT",),
        )

    available_at = snap_ts + pd.Timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
    snapshot_is_future = snap_ts > open_ts
    snapshot_is_in_progress = not snapshot_is_future and available_at > open_ts
    age_seconds = float((open_ts - available_at).total_seconds())
    snapshot_is_stale = not snapshot_is_future and not snapshot_is_in_progress and age_seconds > threshold

    if snapshot_is_future:
        reasons.append(f"BLOCKED_FUTURE_{timeframe.upper()}_SNAPSHOT")
    if snapshot_is_in_progress:
        reasons.append(f"BLOCKED_IN_PROGRESS_{timeframe.upper()}_SNAPSHOT")
    if snapshot_is_stale:
        reasons.append(f"BLOCKED_STALE_{timeframe.upper()}_SNAPSHOT")

    return FreshnessResult(
        timeframe=timeframe,
        trade_open_time=open_ts,
        snapshot_time=snap_ts,
        snapshot_available_at=available_at,
        snapshot_age_seconds=age_seconds,
        snapshot_is_missing=False,
        snapshot_is_future=snapshot_is_future,
        snapshot_is_in_progress=snapshot_is_in_progress,
        snapshot_is_stale=snapshot_is_stale,
        freshness_threshold_seconds=threshold,
        timestamp_semantics=timestamp_semantics,
        status="blocked" if reasons else "ok",
        block_reasons=tuple(reasons),
    )


def evaluate_freshness_frame(
    frame: pd.DataFrame,
    *,
    open_time_column: str = "open_ts",
    max_age_1m_seconds: int = 120,
    max_age_5m_seconds: int = 600,
    timestamp_semantics: str = SNAPSHOT_TIMESTAMP_SEMANTICS,
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        combined: dict[str, Any] = {}
        for timeframe, threshold in (
            ("1m", max_age_1m_seconds),
            ("5m", max_age_5m_seconds),
        ):
            result = evaluate_snapshot_freshness(
                trade_open_time=row.get(open_time_column),
                snapshot_time=row.get(f"open_{timeframe}_ts"),
                timeframe=timeframe,
                max_age_seconds=threshold,
                timestamp_semantics=timestamp_semantics,
            )
            combined.update(result.as_dict())
        records.append(combined)
    return pd.DataFrame(records, index=frame.index)
