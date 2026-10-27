"""Leakage checks for purged walk-forward split records."""

from __future__ import annotations

from datetime import datetime
from numbers import Integral
from typing import Any

import pandas as pd


def audit_leakage(
    frame: pd.DataFrame,
    splits: list[dict[str, Any]],
    *,
    feature_contract: dict[str, Any],
    embargo_seconds: int,
    identity_column: str = "order_id",
) -> dict[str, Any]:
    """Reject invalid evidence and leakage across all three role boundaries.

    Index counters measure shared rows; label counters measure pairs of closed
    information intervals [open, close]. Embargo remains post-evaluation:
    eval.close < earlier-role.open <= eval.close + embargo_seconds.
    """

    boundaries = (("train", "validation"), ("train", "test"), ("validation", "test"))
    direct_counts = {f"{left}_{right}_overlap_count": 0 for left, right in boundaries}
    label_counts = {f"{left}_{right}_label_interval_overlap_count": 0 for left, right in boundaries}
    embargo_violation_count = 0
    duplicated_order_id_across_splits_count = 0
    validation_errors: list[str] = []
    if isinstance(embargo_seconds, bool) or not isinstance(embargo_seconds, Integral) or embargo_seconds < 0:
        validation_errors.append("invalid_embargo_seconds")
    try:
        checked_frame = validated_temporal_frame(frame)
    except ValueError as exc:
        validation_errors.append(str(exc))
        checked_frame = None
    if not splits:
        validation_errors.append("missing_splits")
    for split_number, split in enumerate(splits):
        if not isinstance(split, dict):
            validation_errors.append(f"invalid_split_record:{split_number}")
            continue
        groups: dict[str, set[int]] = {}
        for role in ("train", "validation", "test"):
            indices = split.get(f"_{role}_indices")
            if not isinstance(indices, list) or not indices:
                validation_errors.append(f"invalid_{role}_indices:{split_number}")
                continue
            if any(isinstance(index, bool) or not isinstance(index, Integral) for index in indices):
                validation_errors.append(f"non_integer_{role}_indices:{split_number}")
                continue
            if len(set(indices)) != len(indices) or any(index not in frame.index for index in indices):
                validation_errors.append(f"duplicate_or_unknown_{role}_indices:{split_number}")
                continue
            groups[role] = set(indices)
        if checked_frame is None or len(groups) != 3:
            continue
        referenced = set.union(*groups.values())
        if not isinstance(identity_column, str) or identity_column not in frame.columns:
            validation_errors.append("missing_identity_column")
        elif any(not isinstance(value, str) or not value.strip() or value != value.strip()
                 for value in frame.loc[sorted(referenced), identity_column]):
            validation_errors.append("invalid_stable_identity")
        else:
            duplicated_order_id_across_splits_count += duplicated_order_ids(
                frame, *groups.values(), identity_column=identity_column
            )
        for left, right in boundaries:
            direct_counts[f"{left}_{right}_overlap_count"] += len(groups[left] & groups[right])
            label_counts[f"{left}_{right}_label_interval_overlap_count"] += label_interval_overlaps(
                checked_frame, groups[left], groups[right]
            )
            if checked_frame.loc[sorted(groups[left]), "open_time_utc"].max() > checked_frame.loc[sorted(groups[right]), "open_time_utc"].min():
                validation_errors.append(f"out_of_order_{left}_{right}:{split_number}")
        if "invalid_embargo_seconds" not in validation_errors:
            # Preserve the train-vs-union counter; add the formerly unchecked
            # validation-vs-test boundary without double-counting train pairs.
            try:
                embargo_violation_count += embargo_violations(
                    checked_frame, groups["train"], groups["validation"] | groups["test"], embargo_seconds
                )
                embargo_violation_count += embargo_violations(
                    checked_frame, groups["validation"], groups["test"], embargo_seconds
                )
            except (ValueError, OverflowError) as exc:
                validation_errors.append(f"unrepresentable_embargo:{type(exc).__name__}")

    feature_columns = [str(column) for column in feature_contract.get("feature_columns", []) if isinstance(column, str)]
    future_columns = [column for column in feature_columns if column.lower().startswith("future_ret_")]
    target_columns = [column for column in feature_columns if column.lower().startswith("target_")]
    outcome_candidates = set(str(column) for column in feature_contract.get("outcome_columns", []))
    outcome_candidates.update(str(column) for column in feature_contract.get("label_columns", []))
    outcome_columns = [column for column in feature_columns if column in outcome_candidates or column.lower().startswith("label_")]
    label_interval_overlap_count = sum(label_counts.values())
    leakage_status = "ok"
    if any(
        count > 0
        for count in (
            *direct_counts.values(),
            label_interval_overlap_count,
            embargo_violation_count,
            duplicated_order_id_across_splits_count,
            len(validation_errors),
            len(future_columns),
            len(target_columns),
            len(outcome_columns),
        )
    ):
        leakage_status = "blocked"

    return {
        "temporal_overlap_count": sum(direct_counts.values()),
        **direct_counts,
        **label_counts,
        "embargo_violation_count": embargo_violation_count,
        "duplicated_order_id_across_splits_count": duplicated_order_id_across_splits_count,
        "identity_column": identity_column,
        "label_interval_overlap_count": label_interval_overlap_count,
        "future_columns_in_features_count": len(future_columns),
        "target_columns_in_features_count": len(target_columns),
        "outcome_columns_in_features_count": len(outcome_columns),
        "future_columns_in_features": future_columns,
        "target_columns_in_features": target_columns,
        "outcome_columns_in_features": outcome_columns,
        "leakage_status": leakage_status,
        "validation_errors": sorted(set(validation_errors)),
    }


def validated_temporal_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Return UTC intervals without guessing timezone, units or invalid dates.

    Explicit ISO-8601 offsets and aware datetime values identify instants.
    Missing/naive/numeric timestamps and reversed labels are not evidence.
    The caller receives a copy; source values and row ordering are unchanged.
    """
    if not frame.columns.is_unique or not frame.index.is_unique:
        raise ValueError("duplicate_frame_columns_or_index")
    if any(isinstance(index, bool) or not isinstance(index, Integral) for index in frame.index):
        raise ValueError("non_integer_frame_index")
    normalized = frame.copy()
    for column in ("open_time_utc", "close_time_utc"):
        if column not in frame.columns:
            raise ValueError(f"missing_{column}")
        instants: list[pd.Timestamp] = []
        for value in frame[column]:
            if not isinstance(value, (str, datetime)) or pd.isna(value):
                raise ValueError(f"invalid_{column}")
            try:
                stamp = pd.Timestamp(pd.to_datetime(value, format="ISO8601")) if isinstance(value, str) else pd.Timestamp(value)
                if pd.isna(stamp) or stamp.tzinfo is None:
                    raise ValueError(f"missing_timezone_{column}")
                instants.append(stamp.tz_convert("UTC"))
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"invalid_or_ambiguous_{column}") from exc
        try:
            normalized[column] = pd.Series(instants, index=frame.index, dtype="datetime64[ns, UTC]")
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"unrepresentable_{column}") from exc
    if (normalized["close_time_utc"] < normalized["open_time_utc"]).any():
        raise ValueError("close_before_open")
    return normalized


def label_interval_overlaps(frame: pd.DataFrame, left: set[int], right: set[int]) -> int:
    """Count intersecting cross-role pairs on already validated UTC intervals."""
    if not left or not right:
        return 0
    earlier = frame.loc[sorted(left)]
    later = frame.loc[sorted(right)]
    if earlier["close_time_utc"].max() < later["open_time_utc"].min() or later["close_time_utc"].max() < earlier["open_time_utc"].min():
        return 0
    return sum(
        int(((later["open_time_utc"] <= row.close_time_utc) & (later["close_time_utc"] >= row.open_time_utc)).sum())
        for row in earlier.itertuples()
    )


def interval_intersects(start_a: pd.Timestamp, end_a: pd.Timestamp, start_b: pd.Timestamp, end_b: pd.Timestamp) -> bool:
    return start_a <= end_b and end_a >= start_b


def duplicated_order_ids(frame: pd.DataFrame, *groups: set[int], identity_column: str = "order_id") -> int:
    if identity_column not in frame.columns:
        return 0
    seen: dict[str, int] = {}
    duplicates = 0
    for group_index, group in enumerate(groups):
        values = {
            str(frame.loc[index, identity_column])
            for index in group
            if index in frame.index and str(frame.loc[index, identity_column]).strip()
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
