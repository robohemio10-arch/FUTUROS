from __future__ import annotations

import pandas as pd
import pytest

from smartcrypto.ml.walkforward_split import (
    WalkForwardSplitError,
    create_walkforward_splits,
)


def frame_with_times(rows: int = 18) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_ts": pd.date_range("2026-01-01T00:00:00Z", periods=rows, freq="min"),
            "event_end_ts": pd.date_range("2026-01-01T00:01:00Z", periods=rows, freq="min"),
            "target_win": [idx % 2 for idx in range(rows)],
        }
    )


def test_temporal_split_guarantees_train_end_before_test_start() -> None:
    result = create_walkforward_splits(frame_with_times(), time_column="open_ts", folds=3)

    for fold in result.folds:
        assert pd.Timestamp(fold.train_end) < pd.Timestamp(fold.test_start)
        assert fold.train_rows > 0
        assert fold.test_rows > 0


def test_embargo_creates_gap_between_train_and_test() -> None:
    result = create_walkforward_splits(
        frame_with_times(),
        time_column="open_ts",
        folds=2,
        embargo_seconds=120,
    )

    first = result.folds[0]
    gap = pd.Timestamp(first.test_start) - pd.Timestamp(first.train_end)
    assert gap.total_seconds() >= 120


def test_purging_removes_overlapping_events() -> None:
    frame = frame_with_times()
    frame.loc[0, "event_end_ts"] = pd.Timestamp("2026-01-01T00:10:00Z")

    result = create_walkforward_splits(
        frame,
        time_column="open_ts",
        event_end_column="event_end_ts",
        folds=2,
    )

    assert result.folds[0].purged_rows > 0


def test_split_fails_with_null_timestamp() -> None:
    frame = frame_with_times()
    frame.loc[0, "open_ts"] = pd.NaT

    with pytest.raises(WalkForwardSplitError, match="timestamp_null"):
        create_walkforward_splits(frame, time_column="open_ts")


def test_split_fails_with_missing_time_column() -> None:
    with pytest.raises(WalkForwardSplitError, match="time_column_missing"):
        create_walkforward_splits(frame_with_times(), time_column="missing_ts")
