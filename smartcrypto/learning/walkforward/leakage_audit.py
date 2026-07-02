"""Leakage checks for purged walk-forward split records."""

from __future__ import annotations

from typing import Any

import pandas as pd


def audit_leakage(
    frame: pd.DataFrame,
    splits: list[dict[str, Any]],
    *,
    feature_contract: dict[str, Any],
    embargo_seconds: int,
) -> dict[str, Any]:
    """Audit split index and label-interval leakage."""

    train_validation_overlap_count = 0
    train_test_overlap_count = 0
    embargo_violation_count = 0
    duplicated_order_id_across_splits_count = 0

    for split in splits:
        train_indices = set(split["_train_indices"])
        validation_indices = set(split["_validation_indices"])
        test_indices = set(split["_test_indices"])
        train_validation_overlap_count += len(train_indices & validation_indices)
        train_test_overlap_count += len(train_indices & test_indices)
        duplicated_order_id_across_splits_count += duplicated_order_ids(frame, train_indices, validation_indices, test_indices)
        embargo_violation_count += embargo_violations(frame, train_indices, validation_indices | test_indices, embargo_seconds)

    feature_columns = [str(column) for column in feature_contract.get("feature_columns", []) if isinstance(column, str)]
    future_columns = [column for column in feature_columns if column.lower().startswith("future_ret_")]
    target_columns = [column for column in feature_columns if column.lower().startswith("target_")]
    outcome_candidates = set(str(column) for column in feature_contract.get("outcome_columns", []))
    outcome_candidates.update(str(column) for column in feature_contract.get("label_columns", []))
    outcome_columns = [column for column in feature_columns if column in outcome_candidates or column.lower().startswith("label_")]
    label_interval_overlap_count = train_validation_overlap_count + train_test_overlap_count
    leakage_status = "ok"
    if any(
        count > 0
        for count in (
            train_validation_overlap_count,
            train_test_overlap_count,
            embargo_violation_count,
            len(future_columns),
            len(target_columns),
            len(outcome_columns),
        )
    ):
        leakage_status = "blocked"

    return {
        "temporal_overlap_count": train_validation_overlap_count + train_test_overlap_count,
        "train_validation_overlap_count": train_validation_overlap_count,
        "train_test_overlap_count": train_test_overlap_count,
        "embargo_violation_count": embargo_violation_count,
        "duplicated_order_id_across_splits_count": duplicated_order_id_across_splits_count,
        "label_interval_overlap_count": label_interval_overlap_count,
        "future_columns_in_features_count": len(future_columns),
        "target_columns_in_features_count": len(target_columns),
        "outcome_columns_in_features_count": len(outcome_columns),
        "future_columns_in_features": future_columns,
        "target_columns_in_features": target_columns,
        "outcome_columns_in_features": outcome_columns,
        "leakage_status": leakage_status,
    }


def interval_intersects(start_a: pd.Timestamp, end_a: pd.Timestamp, start_b: pd.Timestamp, end_b: pd.Timestamp) -> bool:
    return start_a <= end_b and end_a >= start_b


def duplicated_order_ids(frame: pd.DataFrame, *groups: set[int]) -> int:
    if "order_id" not in frame.columns:
        return 0
    seen: dict[str, int] = {}
    duplicates = 0
    for group_index, group in enumerate(groups):
        values = {
            str(frame.loc[index, "order_id"])
            for index in group
            if index in frame.index and str(frame.loc[index, "order_id"]).strip()
        }
        for value in values:
            previous = seen.get(value)
            if previous is not None and previous != group_index:
                duplicates += 1
            seen[value] = group_index
    return duplicates


def embargo_violations(frame: pd.DataFrame, train_indices: set[int], eval_indices: set[int], embargo_seconds: int) -> int:
    if not train_indices or not eval_indices:
        return 0
    train = frame.loc[sorted(train_indices)]
    eval_frame = frame.loc[sorted(eval_indices)]
    count = 0
    for _, eval_row in eval_frame.iterrows():
        embargo_start = eval_row["close_time_utc"]
        embargo_end = embargo_start + pd.Timedelta(seconds=embargo_seconds)
        mask = (train["open_time_utc"] > embargo_start) & (train["open_time_utc"] <= embargo_end)
        count += int(mask.sum())
    return count
