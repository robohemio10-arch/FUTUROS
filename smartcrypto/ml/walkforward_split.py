from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd


class WalkForwardSplitError(ValueError):
    pass


@dataclass(frozen=True)
class WalkForwardFold:
    fold_id: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    embargo_seconds: int
    train_rows: int
    test_rows: int
    purged_rows: int
    train_indices: list[int] = field(default_factory=list)
    test_indices: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WalkForwardSplitResult:
    status: str
    time_column: str
    event_end_column: str | None
    folds: list[WalkForwardFold]
    checked_at: str = field(default_factory=lambda: utc_timestamp())

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "time_column": self.time_column,
            "event_end_column": self.event_end_column,
            "folds": [fold.to_dict() for fold in self.folds],
            "checked_at": self.checked_at,
        }


def create_walkforward_splits(
    frame: pd.DataFrame,
    *,
    time_column: str,
    folds: int = 5,
    embargo_seconds: int = 0,
    event_end_column: str | None = None,
) -> WalkForwardSplitResult:
    if not isinstance(frame, pd.DataFrame):
        raise WalkForwardSplitError("walkforward_input_must_be_dataframe")
    if time_column not in frame.columns:
        raise WalkForwardSplitError(f"time_column_missing:{time_column}")
    if event_end_column and event_end_column not in frame.columns:
        raise WalkForwardSplitError(f"event_end_column_missing:{event_end_column}")
    if folds < 1:
        raise WalkForwardSplitError("folds_must_be_positive")
    if embargo_seconds < 0:
        raise WalkForwardSplitError("embargo_seconds_must_be_non_negative")

    working = frame.copy()
    working["_phase23_original_index"] = list(frame.index)
    working["_phase23_time"] = pd.to_datetime(
        working[time_column],
        utc=True,
        errors="coerce",
    )
    if working["_phase23_time"].isna().any():
        raise WalkForwardSplitError("timestamp_null_or_unparseable")

    if event_end_column:
        working["_phase23_event_end"] = pd.to_datetime(
            working[event_end_column],
            utc=True,
            errors="coerce",
        )
        if working["_phase23_event_end"].isna().any():
            raise WalkForwardSplitError("event_end_timestamp_null_or_unparseable")
    else:
        working["_phase23_event_end"] = working["_phase23_time"]

    working = working.sort_values("_phase23_time", kind="stable").reset_index(drop=True)
    row_count = len(working)
    if row_count < folds + 1:
        raise WalkForwardSplitError("insufficient_rows_for_walkforward_splits")

    test_size = max(1, row_count // (folds + 1))
    fold_reports: list[WalkForwardFold] = []
    embargo_delta = pd.Timedelta(seconds=int(embargo_seconds))

    for fold_id in range(1, folds + 1):
        test_start_pos = test_size * fold_id
        test_end_pos = min(row_count, test_start_pos + test_size)
        if test_start_pos >= row_count or test_end_pos <= test_start_pos:
            break

        train_candidate = working.iloc[:test_start_pos].copy()
        test = working.iloc[test_start_pos:test_end_pos].copy()
        if test.empty:
            continue

        test_start = test["_phase23_time"].min()
        test_end = test["_phase23_time"].max()
        embargo_cutoff = test_start - embargo_delta

        keep_mask = train_candidate["_phase23_time"] < embargo_cutoff
        purged_mask = train_candidate["_phase23_event_end"] >= test_start
        final_train = train_candidate[keep_mask & ~purged_mask]
        purged_rows = int(len(train_candidate) - len(final_train))

        if final_train.empty:
            raise WalkForwardSplitError(f"empty_train_after_embargo_or_purging:{fold_id}")
        train_end = final_train["_phase23_time"].max()
        if train_end >= test_start:
            raise WalkForwardSplitError(f"train_test_overlap_detected:{fold_id}")
        if test_start <= final_train["_phase23_time"].min():
            raise WalkForwardSplitError(f"test_not_after_train:{fold_id}")

        fold_reports.append(
            WalkForwardFold(
                fold_id=fold_id,
                train_start=to_iso(final_train["_phase23_time"].min()),
                train_end=to_iso(train_end),
                test_start=to_iso(test_start),
                test_end=to_iso(test_end),
                embargo_seconds=int(embargo_seconds),
                train_rows=int(len(final_train)),
                test_rows=int(len(test)),
                purged_rows=purged_rows,
                train_indices=[
                    int(value) for value in final_train["_phase23_original_index"].tolist()
                ],
                test_indices=[int(value) for value in test["_phase23_original_index"].tolist()],
            )
        )

    if not fold_reports:
        raise WalkForwardSplitError("no_valid_walkforward_folds")

    return WalkForwardSplitResult(
        status="OK",
        time_column=time_column,
        event_end_column=event_end_column,
        folds=fold_reports,
    )


def to_iso(value: pd.Timestamp) -> str:
    return value.isoformat().replace("+00:00", "Z")


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
