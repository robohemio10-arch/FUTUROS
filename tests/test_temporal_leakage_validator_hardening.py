"""Synthetic, offline regression tests for fail-closed temporal split audits."""

from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
import pytest

from smartcrypto.learning.walkforward.leakage_audit import audit_leakage
from smartcrypto.learning.walkforward.purged_split_engine import (
    embargo_indices,
    indices_hash,
    purge_indices,
    validate_raw_source,
)
from smartcrypto.research.trades_master_official.qlib_dataset import (
    _true_label_interval_overlap_count,
)


ROLES = ("train", "validation", "test")
ROLE_PAIRS = (("train", "validation"), ("train", "test"), ("validation", "test"))


def frame_for(intervals: list[tuple[int, int]]) -> pd.DataFrame:
    origin = pd.Timestamp("2026-01-01T00:00:00Z")
    return pd.DataFrame(
        {
            "open_time_utc": [origin + pd.Timedelta(seconds=start) for start, _ in intervals],
            "close_time_utc": [origin + pd.Timedelta(seconds=end) for _, end in intervals],
            "order_id": [f"synthetic-order-{number}" for number in range(len(intervals))],
        }
    )


def split(
    train: list[int] | None = None,
    validation: list[int] | None = None,
    test: list[int] | None = None,
) -> dict[str, Any]:
    return {
        "_train_indices": [0] if train is None else train,
        "_validation_indices": [1] if validation is None else validation,
        "_test_indices": [2] if test is None else test,
    }


def audit(
    frame: pd.DataFrame,
    record: dict[str, Any] | None = None,
    *,
    embargo_seconds: int = 86_400,
) -> dict[str, Any]:
    return audit_leakage(
        frame,
        [split() if record is None else record],
        feature_contract={"feature_columns": ["feature_past_return"]},
        embargo_seconds=embargo_seconds,
    )


def assert_invalid(result: dict[str, Any]) -> None:
    assert result["leakage_status"] == "blocked"
    assert result["validation_errors"]


@pytest.mark.parametrize("left,right", ROLE_PAIRS)
def test_direct_membership_overlap_blocks_every_boundary(left: str, right: str) -> None:
    record = split()
    record[f"_{right}_indices"] = record[f"_{left}_indices"].copy()
    result = audit(frame_for([(0, 10), (100, 110), (200, 210)]), record)

    assert result[f"{left}_{right}_overlap_count"] == 1
    assert result["temporal_overlap_count"] == 1
    assert result["leakage_status"] == "blocked"


@pytest.mark.parametrize("left,right", ROLE_PAIRS)
def test_distinct_identity_label_overlap_blocks_every_boundary(left: str, right: str) -> None:
    intervals = [(0, 10), (100, 110), (200, 210)]
    intervals[ROLES.index(left)] = (50, 150)
    intervals[ROLES.index(right)] = (100, 120)
    frame = frame_for(intervals)
    result = audit(frame)

    assert frame.order_id.is_unique
    assert result["temporal_overlap_count"] == 0
    assert result[f"{left}_{right}_label_interval_overlap_count"] == 1
    assert result["label_interval_overlap_count"] >= 1
    assert result["leakage_status"] == "blocked"


@pytest.mark.parametrize("closing_second,expected", [(99, 0), (100, 1), (101, 1)])
def test_label_interval_endpoints_are_inclusive(closing_second: int, expected: int) -> None:
    frame = frame_for([(0, closing_second), (100, 110), (200, 210)])
    result = audit(frame)

    assert result["train_validation_label_interval_overlap_count"] == expected
    assert purge_indices(frame, [0], [1, 2]) == ({0} if expected else set())
    assert (result["leakage_status"] == "blocked") is bool(expected)


def test_label_counter_counts_interval_pairs_instead_of_index_overlap() -> None:
    frame = frame_for([(0, 200), (50, 150), (100, 120), (130, 170)])
    record = split([0, 1], [2], [3])
    result = audit(frame, record)

    assert result["temporal_overlap_count"] == 0
    assert result["train_validation_label_interval_overlap_count"] == 2
    assert result["train_test_label_interval_overlap_count"] == 2
    assert result["validation_test_label_interval_overlap_count"] == 0
    assert result["label_interval_overlap_count"] == 4
    assert result["leakage_status"] == "blocked"


def test_wq6_helper_includes_validation_test_true_interval_overlap() -> None:
    frame = frame_for([(0, 10), (100, 220), (200, 210)])

    assert _true_label_interval_overlap_count(frame, [split()]) == 1
    assert audit(frame)["validation_test_label_interval_overlap_count"] == 1


@pytest.mark.parametrize("left,right", ROLE_PAIRS)
def test_duplicate_identity_blocks_disjoint_indices_and_times(left: str, right: str) -> None:
    frame = frame_for([(0, 10), (100, 110), (200, 210)])
    frame.loc[ROLES.index(right), "order_id"] = frame.loc[ROLES.index(left), "order_id"]
    result = audit(frame)

    assert result["temporal_overlap_count"] == 0
    assert result["label_interval_overlap_count"] == 0
    assert result["duplicated_order_id_across_splits_count"] == 1
    assert result["leakage_status"] == "blocked"


@pytest.mark.parametrize("delta,expected", [(0, 0), (1, 1), (86_400, 1), (86_401, 0)])
def test_post_evaluation_embargo_endpoints(delta: int, expected: int) -> None:
    frame = frame_for([(0, 100), (100 + delta, 110 + delta)])

    assert embargo_indices(frame, [1], [0], 86_400) == ({1} if expected else set())


def test_validation_test_post_evaluation_embargo_is_audited() -> None:
    # Validation opens after the test label closes: no label/index overlap,
    # but it falls inside the existing post-evaluation embargo direction.
    frame = frame_for([(0, 10), (301, 310), (200, 300)])
    result = audit(frame)

    assert result["label_interval_overlap_count"] == 0
    assert result["embargo_violation_count"] >= 1
    assert result["leakage_status"] == "blocked"


def test_forward_roles_do_not_acquire_an_implicit_one_day_gap() -> None:
    result = audit(frame_for([(0, 10), (11, 20), (21, 30)]))

    assert result["embargo_violation_count"] == 0
    assert result["leakage_status"] == "ok"


@pytest.mark.parametrize("column", ["open_time_utc", "close_time_utc"])
@pytest.mark.parametrize(
    "invalid",
    [pd.NaT, None, "not-a-time", "2026-01-01T00:00:00", "01/02/2026 00:00:00", 1_767_225_600, True],
)
def test_invalid_or_ambiguous_timestamp_fails_closed(column: str, invalid: object) -> None:
    frame = frame_for([(0, 10), (100, 110), (200, 210)])
    frame[column] = frame[column].astype(object)
    frame.loc[1, column] = invalid

    assert_invalid(audit(frame))


@pytest.mark.parametrize("column", ["open_time_utc", "close_time_utc"])
def test_missing_timestamp_column_fails_closed(column: str) -> None:
    frame = frame_for([(0, 10), (100, 110), (200, 210)]).drop(columns=column)

    assert_invalid(audit(frame))


def test_reversed_label_interval_fails_closed() -> None:
    assert_invalid(audit(frame_for([(0, 10), (110, 100), (200, 210)])))


def test_explicit_timezone_offsets_preserve_the_instant() -> None:
    frame = frame_for([(0, 10), (100, 110), (200, 210)])
    for column in ("open_time_utc", "close_time_utc"):
        frame[column] = [stamp.tz_convert("America/Sao_Paulo").isoformat() for stamp in frame[column]]
    before = frame.copy(deep=True)

    result = audit(frame)

    assert result["leakage_status"] == "ok"
    pd.testing.assert_frame_equal(frame, before)


@pytest.mark.parametrize("column", ["open_time_utc", "close_time_utc"])
def test_raw_source_rejects_naive_time_before_utc_conversion(column: str) -> None:
    frame = frame_for([(0, 10), (100, 110), (200, 210)])
    frame[column] = frame[column].dt.tz_localize(None)

    assert validate_raw_source(frame)


@pytest.mark.parametrize("invalid", [[1, 1], [99], [True], [1.0], ["1"], []])
def test_invalid_membership_fails_closed(invalid: list[object]) -> None:
    record = split()
    record["_validation_indices"] = invalid

    assert_invalid(audit(frame_for([(0, 10), (100, 110), (200, 210)]), record))


def test_missing_membership_role_fails_closed() -> None:
    record = split()
    del record["_test_indices"]

    assert_invalid(audit(frame_for([(0, 10), (100, 110), (200, 210)]), record))


def test_duplicate_frame_index_fails_closed() -> None:
    frame = frame_for([(0, 10), (100, 110), (200, 210)])
    frame.index = [0, 1, 1]

    assert_invalid(audit(frame))


@pytest.mark.parametrize("invalid", [None, pd.NA, np.nan, "", "   "])
def test_absent_stable_identity_fails_closed(invalid: object) -> None:
    frame = frame_for([(0, 10), (100, 110), (200, 210)])
    frame.loc[1, "order_id"] = invalid

    assert_invalid(audit(frame))


def test_missing_stable_identity_column_fails_closed() -> None:
    frame = frame_for([(0, 10), (100, 110), (200, 210)]).drop(columns="order_id")

    assert_invalid(audit(frame))


def test_default_identity_does_not_infer_an_alternative_column() -> None:
    frame = frame_for([(0, 10), (100, 110), (200, 210)]).rename(
        columns={"order_id": "estimate_subject_id"}
    )
    before = frame.copy(deep=True)

    assert_invalid(audit(frame))
    pd.testing.assert_frame_equal(frame, before)


@pytest.mark.parametrize("duplicate", [False, True])
def test_explicit_subject_identity_is_used_without_reconstruction(duplicate: bool) -> None:
    frame = frame_for([(0, 10), (100, 110), (200, 210)]).rename(
        columns={"order_id": "estimate_subject_id"}
    )
    if duplicate:
        frame.loc[2, "estimate_subject_id"] = frame.loc[1, "estimate_subject_id"]
    before = frame.copy(deep=True)

    result = audit_leakage(
        frame,
        [split()],
        feature_contract={"feature_columns": ["feature_past_return"]},
        embargo_seconds=86_400,
        identity_column="estimate_subject_id",
    )

    assert result["leakage_status"] == ("blocked" if duplicate else "ok")
    assert result["duplicated_order_id_across_splits_count"] == int(duplicate)
    assert "order_id" not in frame.columns
    pd.testing.assert_frame_equal(frame, before)


@pytest.mark.parametrize("duplicate", [False, True])
def test_financial_consumer_explicitly_selects_its_existing_subject_identity(duplicate: bool) -> None:
    # Extract this adapter only. Never import or execute the financial engine,
    # its evaluators, backtests, model classes or training entry points.
    path = (
        Path(__file__).resolve().parents[1]
        / "smartcrypto/research/financial_ai_research_engine/engine.py"
    )
    parsed = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    functions = [
        node
        for node in parsed.body
        if isinstance(node, ast.FunctionDef) and node.name == "_anti_leakage_report"
    ]
    assert len(functions) == 1
    calls = {ast.unparse(node.func) for node in ast.walk(functions[0]) if isinstance(node, ast.Call)}
    assert calls <= {"list", "feature_contract.get", "audit_leakage"}
    isolated = ast.Module(body=functions, type_ignores=[])
    ast.fix_missing_locations(isolated)
    namespace = {"pd": pd, "Any": Any, "Mapping": Mapping, "audit_leakage": audit_leakage}
    exec(compile(isolated, str(path), "exec"), namespace)
    frame = frame_for([(0, 10), (100, 110), (200, 210)]).rename(
        columns={"order_id": "estimate_subject_id"}
    )
    if duplicate:
        frame.loc[2, "estimate_subject_id"] = frame.loc[1, "estimate_subject_id"]
    before = frame.copy(deep=True)

    result = namespace["_anti_leakage_report"](
        frame,
        [split()],
        feature_contract={"feature_columns": ["feature_past_return"]},
        embargo_seconds=86_400,
    )

    assert result["leakage_status"] == ("blocked" if duplicate else "ok")
    assert result["reason"] == "institutional_leakage_audit"
    assert result["duplicated_order_id_across_splits_count"] == int(duplicate)
    pd.testing.assert_frame_equal(frame, before)


@pytest.mark.parametrize("invalid", [-1, True, 1.5])
def test_invalid_embargo_policy_fails_closed(invalid: object) -> None:
    assert_invalid(audit(frame_for([(0, 10), (100, 110), (200, 210)]), embargo_seconds=invalid))


def test_unrepresentable_embargo_duration_returns_controlled_block() -> None:
    assert_invalid(audit(frame_for([(0, 10), (100, 110), (200, 210)]), embargo_seconds=10**100))


def test_embargo_past_timestamp_limit_returns_controlled_block() -> None:
    frame = frame_for([(0, 10), (100, 110), (200, 210)])
    origin = pd.Timestamp.max.tz_localize("UTC") - pd.Timedelta(hours=1)
    frame["open_time_utc"] = [origin + pd.Timedelta(seconds=offset) for offset in (0, 100, 200)]
    frame["close_time_utc"] = [origin + pd.Timedelta(seconds=offset) for offset in (10, 110, 210)]

    assert_invalid(audit(frame))


def test_shared_past_candle_does_not_imply_label_leakage_or_mutate_membership() -> None:
    frame = frame_for([(0, 60), (30 * 60, 31 * 60), (60 * 60, 61 * 60)])
    frame["feature_cutoff_utc"] = frame.open_time_utc
    frame["symbol"] = "BTCUSDT"
    record = split()
    before_record = deepcopy(record)
    before_frame = frame.copy(deep=True)
    hashes = {key: indices_hash(value) for key, value in record.items()}

    # Each feature may use31 closed bars; adjacent entries30min apart share
    # one historical raw candle while their label intervals remain disjoint.
    assert frame.open_time_utc.iloc[1] - pd.Timedelta(minutes=31) < frame.open_time_utc.iloc[0]
    result = audit(frame, record)

    assert result["leakage_status"] == "ok"
    assert result["label_interval_overlap_count"] == 0
    assert record == before_record
    assert {key: indices_hash(value) for key, value in record.items()} == hashes
    pd.testing.assert_frame_equal(frame, before_frame)
