"""Deterministic temporal split, purging, embargo, and CPCV group contracts."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Any, Iterable, Sequence

import pandas as pd

from .contracts import SplitMode, StepEvidence, StepStatus, TemporalSplitContract, stable_hash


@dataclass(frozen=True)
class TemporalFold:
    fold_id: str
    train_indices: tuple[int, ...]
    validation_indices: tuple[int, ...]
    test_indices: tuple[int, ...]
    purged_indices: tuple[int, ...]
    embargoed_indices: tuple[int, ...]
    train_start: str
    train_end: str
    validation_start: str
    validation_end: str
    test_start: str
    test_end: str
    fold_hash: str

    def to_dict(self, *, include_indices: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "fold_id": self.fold_id,
            "train_start": self.train_start,
            "train_end": self.train_end,
            "validation_start": self.validation_start,
            "validation_end": self.validation_end,
            "test_start": self.test_start,
            "test_end": self.test_end,
            "train_rows": len(self.train_indices),
            "validation_rows": len(self.validation_indices),
            "test_rows": len(self.test_indices),
            "purged_row_count": len(self.purged_indices),
            "embargoed_row_count": len(self.embargoed_indices),
            "fold_hash": self.fold_hash,
        }
        if include_indices:
            payload.update(
                {
                    "train_indices": list(self.train_indices),
                    "validation_indices": list(self.validation_indices),
                    "test_indices": list(self.test_indices),
                    "purged_indices": list(self.purged_indices),
                    "embargoed_indices": list(self.embargoed_indices),
                }
            )
        return payload


@dataclass(frozen=True)
class SplitResult:
    folds: tuple[TemporalFold, ...]
    split_hash: str | None
    evidence: StepEvidence


def build_temporal_splits(frame: pd.DataFrame, contract: TemporalSplitContract) -> SplitResult:
    errors = contract.validate()
    if errors:
        evidence = StepEvidence(
            step="temporal_split",
            status=StepStatus.BLOCKED,
            reason="invalid_split_contract",
            blockers=tuple(errors),
        )
        return SplitResult((), None, evidence)

    required = {"open_time_utc", "close_time_utc"}
    missing = sorted(required - set(frame.columns))
    if missing:
        evidence = StepEvidence(
            step="temporal_split",
            status=StepStatus.BLOCKED,
            reason="missing_temporal_columns",
            blockers=tuple(f"missing_column:{item}" for item in missing),
        )
        return SplitResult((), None, evidence)

    data = frame.copy().reset_index(drop=True)
    data["open_time_utc"] = pd.to_datetime(data["open_time_utc"], utc=True, errors="coerce")
    data["close_time_utc"] = pd.to_datetime(data["close_time_utc"], utc=True, errors="coerce")
    if data[["open_time_utc", "close_time_utc"]].isna().any().any():
        evidence = StepEvidence(
            step="temporal_split",
            status=StepStatus.BLOCKED,
            reason="invalid_temporal_values",
            blockers=("invalid_temporal_values",),
        )
        return SplitResult((), None, evidence)
    data = data.sort_values(["open_time_utc", "close_time_utc"], kind="mergesort").reset_index(drop=True)

    required_rows = (
        contract.minimum_train_rows
        + contract.fold_count * (contract.validation_rows + contract.test_rows)
    )
    if len(data) < required_rows:
        evidence = StepEvidence(
            step="temporal_split",
            status=StepStatus.BLOCKED_INSUFFICIENT_SAMPLE,
            reason="insufficient_rows_for_requested_folds",
            metrics={"observed_rows": len(data), "required_rows": required_rows},
            blockers=("insufficient_rows_for_requested_folds",),
        )
        return SplitResult((), None, evidence)

    folds: list[TemporalFold] = []
    for fold_number in range(contract.fold_count):
        validation_start = contract.minimum_train_rows + fold_number * (
            contract.validation_rows + contract.test_rows
        )
        validation_end = validation_start + contract.validation_rows
        test_start = validation_end
        test_end = test_start + contract.test_rows
        if test_end > len(data):
            break

        if contract.mode is SplitMode.ROLLING:
            train_start = max(0, validation_start - contract.rolling_train_rows)
        else:
            train_start = 0
        train_candidates = tuple(range(train_start, validation_start))
        validation_indices = tuple(range(validation_start, validation_end))
        test_indices = tuple(range(test_start, test_end))
        evaluation_indices = validation_indices + test_indices

        purged = _purged_indices(data, train_candidates, evaluation_indices, contract)
        after_purge = tuple(index for index in train_candidates if index not in purged)
        embargoed = _embargoed_indices(data, after_purge, evaluation_indices, contract)
        train_indices = tuple(index for index in after_purge if index not in embargoed)

        if len(train_indices) < contract.minimum_train_rows:
            continue
        if _overlap_exists(train_indices, validation_indices, test_indices):
            evidence = StepEvidence(
                step="temporal_split",
                status=StepStatus.BLOCKED,
                reason="split_index_overlap",
                blockers=("split_index_overlap",),
            )
            return SplitResult((), None, evidence)

        fold_payload = {
            "fold_number": fold_number + 1,
            "mode": contract.mode.value,
            "train_indices": list(train_indices),
            "validation_indices": list(validation_indices),
            "test_indices": list(test_indices),
            "purged_indices": sorted(purged),
            "embargoed_indices": sorted(embargoed),
            "contract": {
                "purge_seconds": contract.purge_seconds,
                "embargo_seconds": contract.embargo_seconds,
                "feature_lookback_seconds": contract.feature_lookback_seconds,
                "label_horizon_seconds": contract.label_horizon_seconds,
            },
        }
        fold_hash = stable_hash(fold_payload)
        folds.append(
            TemporalFold(
                fold_id=f"fold_{fold_number + 1:03d}",
                train_indices=train_indices,
                validation_indices=validation_indices,
                test_indices=test_indices,
                purged_indices=tuple(sorted(purged)),
                embargoed_indices=tuple(sorted(embargoed)),
                train_start=_iso(data, train_indices[0], "open_time_utc"),
                train_end=_iso(data, train_indices[-1], "close_time_utc"),
                validation_start=_iso(data, validation_indices[0], "open_time_utc"),
                validation_end=_iso(data, validation_indices[-1], "close_time_utc"),
                test_start=_iso(data, test_indices[0], "open_time_utc"),
                test_end=_iso(data, test_indices[-1], "close_time_utc"),
                fold_hash=fold_hash,
            )
        )

    if len(folds) != contract.fold_count:
        evidence = StepEvidence(
            step="temporal_split",
            status=StepStatus.BLOCKED_INSUFFICIENT_SAMPLE,
            reason="valid_fold_count_below_requested",
            metrics={"valid_fold_count": len(folds), "requested_fold_count": contract.fold_count},
            blockers=("valid_fold_count_below_requested",),
        )
        return SplitResult(tuple(folds), None, evidence)

    payload = {
        "contract": {
            **contract.__dict__,
            "mode": contract.mode.value,
        },
        "folds": [fold.to_dict(include_indices=True) for fold in folds],
    }
    result_hash = stable_hash(payload)
    evidence = StepEvidence(
        step="temporal_split",
        status=StepStatus.PASS,
        reason="temporal_split_ok",
        metrics={
            "mode": contract.mode.value,
            "fold_count": len(folds),
            "split_hash": result_hash,
            "purged_row_count": sum(len(fold.purged_indices) for fold in folds),
            "embargoed_row_count": sum(len(fold.embargoed_indices) for fold in folds),
        },
    )
    return SplitResult(tuple(folds), result_hash, evidence)


def build_cpcv_paths(row_count: int, group_count: int, test_group_count: int) -> tuple[dict[str, Any], ...]:
    if row_count <= 0 or group_count < 3 or not 0 < test_group_count < group_count:
        return ()
    groups = _contiguous_groups(row_count, group_count)
    paths: list[dict[str, Any]] = []
    for path_number, test_groups in enumerate(combinations(range(group_count), test_group_count), start=1):
        test_set = set(test_groups)
        test_indices = tuple(index for group in test_groups for index in groups[group])
        train_indices = tuple(
            index for group_number, group in enumerate(groups) if group_number not in test_set for index in group
        )
        payload = {
            "path_number": path_number,
            "train_groups": [index for index in range(group_count) if index not in test_set],
            "test_groups": list(test_groups),
            "train_indices": list(train_indices),
            "test_indices": list(test_indices),
        }
        paths.append({**payload, "path_hash": stable_hash(payload)})
    return tuple(paths)


def purge_cpcv_path(
    frame: pd.DataFrame,
    path: dict[str, Any],
    contract: TemporalSplitContract,
) -> dict[str, Any]:
    train_indices = tuple(int(item) for item in path["train_indices"])
    test_indices = tuple(int(item) for item in path["test_indices"])
    purged = _purged_indices(frame, train_indices, test_indices, contract)
    after_purge = tuple(index for index in train_indices if index not in purged)
    embargoed = _cpcv_embargoed_indices(frame, after_purge, test_indices, contract)
    final_train = tuple(index for index in after_purge if index not in embargoed)
    payload = {
        **path,
        "train_indices": list(final_train),
        "purged_indices": sorted(purged),
        "embargoed_indices": sorted(embargoed),
    }
    return {**payload, "path_hash": stable_hash(payload)}


def _purged_indices(
    frame: pd.DataFrame,
    train_indices: Sequence[int],
    evaluation_indices: Sequence[int],
    contract: TemporalSplitContract,
) -> set[int]:
    purged: set[int] = set()
    eval_intervals = [_information_interval(frame, index, contract) for index in evaluation_indices]
    for index in train_indices:
        train_interval = _information_interval(frame, index, contract)
        if any(_intersects(train_interval, evaluation) for evaluation in eval_intervals):
            purged.add(index)
    return purged


def _embargoed_indices(
    frame: pd.DataFrame,
    train_indices: Sequence[int],
    evaluation_indices: Sequence[int],
    contract: TemporalSplitContract,
) -> set[int]:
    if not evaluation_indices or contract.embargo_seconds <= 0:
        return set()
    embargoed: set[int] = set()
    eval_start = min(frame.loc[index, "open_time_utc"] for index in evaluation_indices)
    boundary = eval_start - pd.Timedelta(seconds=contract.embargo_seconds)
    for index in train_indices:
        if frame.loc[index, "close_time_utc"] > boundary:
            embargoed.add(index)
    return embargoed


def _cpcv_embargoed_indices(
    frame: pd.DataFrame,
    train_indices: Sequence[int],
    test_indices: Sequence[int],
    contract: TemporalSplitContract,
) -> set[int]:
    """Apply post-test embargo around every contiguous CPCV test block."""

    if not test_indices or contract.embargo_seconds <= 0:
        return set()
    test_blocks = _contiguous_index_blocks(test_indices)
    embargoed: set[int] = set()
    for block in test_blocks:
        block_end = max(frame.loc[index, "close_time_utc"] for index in block)
        embargo_end = block_end + pd.Timedelta(seconds=contract.embargo_seconds)
        for index in train_indices:
            train_start = frame.loc[index, "open_time_utc"]
            if block_end < train_start <= embargo_end:
                embargoed.add(index)
    return embargoed


def _contiguous_index_blocks(indices: Sequence[int]) -> tuple[tuple[int, ...], ...]:
    ordered = sorted(set(int(item) for item in indices))
    if not ordered:
        return ()
    blocks: list[list[int]] = [[ordered[0]]]
    for index in ordered[1:]:
        if index == blocks[-1][-1] + 1:
            blocks[-1].append(index)
        else:
            blocks.append([index])
    return tuple(tuple(block) for block in blocks)


def _information_interval(
    frame: pd.DataFrame,
    index: int,
    contract: TemporalSplitContract,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = frame.loc[index, "open_time_utc"] - pd.Timedelta(
        seconds=contract.feature_lookback_seconds
    )
    end = frame.loc[index, "close_time_utc"] + pd.Timedelta(
        seconds=contract.label_horizon_seconds + contract.purge_seconds
    )
    if "label_end_time_utc" in frame.columns and pd.notna(frame.loc[index, "label_end_time_utc"]):
        end = max(end, pd.Timestamp(frame.loc[index, "label_end_time_utc"]))
    return start, end


def _intersects(left: tuple[pd.Timestamp, pd.Timestamp], right: tuple[pd.Timestamp, pd.Timestamp]) -> bool:
    return left[0] <= right[1] and right[0] <= left[1]


def _overlap_exists(*groups: Iterable[int]) -> bool:
    seen: set[int] = set()
    for group in groups:
        current = set(group)
        if seen.intersection(current):
            return True
        seen.update(current)
    return False


def _contiguous_groups(row_count: int, group_count: int) -> tuple[tuple[int, ...], ...]:
    base, remainder = divmod(row_count, group_count)
    groups: list[tuple[int, ...]] = []
    cursor = 0
    for group_number in range(group_count):
        size = base + (1 if group_number < remainder else 0)
        groups.append(tuple(range(cursor, cursor + size)))
        cursor += size
    return tuple(groups)


def _iso(frame: pd.DataFrame, index: int, column: str) -> str:
    return pd.Timestamp(frame.loc[index, column]).isoformat()
