"""Calibration metrics for Qlib rank scores and AI Shadow probabilities."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


def rank_percentile_probabilities(values: Sequence[float]) -> list[float]:
    """Convert arbitrary finite scores into deterministic mid-rank percentiles."""

    if not values:
        return []
    indexed = [(float(value), index) for index, value in enumerate(values)]
    for value, _index in indexed:
        if not math.isfinite(value):
            raise ValueError("qlib scores must be finite")
    ordered = sorted(indexed, key=lambda item: (item[0], item[1]))
    output = [0.0] * len(values)
    cursor = 0
    denominator = max(1, len(values) - 1)
    while cursor < len(ordered):
        end = cursor + 1
        while end < len(ordered) and ordered[end][0] == ordered[cursor][0]:
            end += 1
        mid_rank = (cursor + end - 1) / 2.0
        percentile = mid_rank / denominator if len(values) > 1 else 0.5
        for position in range(cursor, end):
            output[ordered[position][1]] = round(percentile, 12)
        cursor = end
    return output


def build_calibration_suite(
    rows: Sequence[Mapping[str, Any]],
    *,
    bin_count: int,
    min_bucket_rows: int,
) -> dict[str, Any]:
    if bin_count <= 0:
        raise ValueError("bin_count must be positive")
    if min_bucket_rows <= 0:
        raise ValueError("min_bucket_rows must be positive")
    qlib_scores = [_finite_float(row.get("qlib_score"), "qlib_score") for row in rows]
    qlib_probabilities = rank_percentile_probabilities(qlib_scores)
    shadow_probabilities = [
        _probability(row.get("ai_shadow_probability"), "ai_shadow_probability")
        for row in rows
    ]
    labels = [_binary_label(row.get("label")) for row in rows]
    pnls = [_finite_float(row.get("net_pnl"), "net_pnl") for row in rows]
    ensemble_probabilities = [
        round((qlib + shadow) / 2.0, 12)
        for qlib, shadow in zip(qlib_probabilities, shadow_probabilities, strict=True)
    ]
    return {
        "qlib_ranker": calibration_report(
            qlib_probabilities,
            labels,
            pnls,
            bin_count=bin_count,
            min_bucket_rows=min_bucket_rows,
            score_semantics="rank_percentile_probability_proxy_research_only",
        ),
        "ai_shadow_veto": calibration_report(
            shadow_probabilities,
            labels,
            pnls,
            bin_count=bin_count,
            min_bucket_rows=min_bucket_rows,
            score_semantics="probability_quality_research_only",
        ),
        "ensemble": calibration_report(
            ensemble_probabilities,
            labels,
            pnls,
            bin_count=bin_count,
            min_bucket_rows=min_bucket_rows,
            score_semantics="mean_rank_percentile_and_probability_quality_research_only",
        ),
        "row_count": len(rows),
        "calibration_applied_to_runtime": False,
        "thresholds_applied_to_runtime": False,
    }


def calibration_report(
    probabilities: Sequence[float],
    labels: Sequence[int],
    pnls: Sequence[float],
    *,
    bin_count: int,
    min_bucket_rows: int,
    score_semantics: str,
) -> dict[str, Any]:
    if not (len(probabilities) == len(labels) == len(pnls)):
        raise ValueError("calibration vectors must have equal length")
    if not probabilities:
        return {
            "status": "insufficient_data",
            "score_semantics": score_semantics,
            "row_count": 0,
            "brier_score": None,
            "expected_calibration_error": None,
            "overall_precision": None,
            "overall_expected_value": None,
            "reliability_curve": [],
            "precision_by_bucket": [],
            "expected_value_by_bucket": [],
            "warnings": ["calibration_rows_missing"],
        }
    checked_probabilities = [_probability(value, "probability") for value in probabilities]
    checked_labels = [_binary_label(value) for value in labels]
    checked_pnls = [_finite_float(value, "net_pnl") for value in pnls]
    buckets = _build_buckets(
        checked_probabilities,
        checked_labels,
        checked_pnls,
        bin_count=bin_count,
    )
    brier = sum(
        (probability - label) ** 2
        for probability, label in zip(checked_probabilities, checked_labels, strict=True)
    ) / len(checked_probabilities)
    ece = sum(
        bucket["count"] / len(checked_probabilities)
        * abs(bucket["mean_predicted_probability"] - bucket["observed_positive_rate"])
        for bucket in buckets
        if bucket["count"] > 0
    )
    total_positive = sum(checked_labels)
    warnings = [
        f"bucket_under_minimum:{bucket['bucket_id']}"
        for bucket in buckets
        if 0 < bucket["count"] < min_bucket_rows
    ]
    return {
        "status": "ok" if not warnings else "warning",
        "score_semantics": score_semantics,
        "row_count": len(checked_probabilities),
        "brier_score": round(brier, 12),
        "expected_calibration_error": round(ece, 12),
        "overall_precision": round(total_positive / len(checked_labels), 12),
        "overall_expected_value": round(sum(checked_pnls) / len(checked_pnls), 12),
        "reliability_curve": buckets,
        "precision_by_bucket": [
            {
                "bucket_id": bucket["bucket_id"],
                "count": bucket["count"],
                "precision": bucket["observed_positive_rate"],
            }
            for bucket in buckets
        ],
        "expected_value_by_bucket": [
            {
                "bucket_id": bucket["bucket_id"],
                "count": bucket["count"],
                "expected_value": bucket["expected_value"],
                "total_pnl": bucket["total_pnl"],
            }
            for bucket in buckets
        ],
        "warnings": warnings,
    }


def _build_buckets(
    probabilities: Sequence[float],
    labels: Sequence[int],
    pnls: Sequence[float],
    *,
    bin_count: int,
) -> list[dict[str, Any]]:
    raw: list[list[int]] = [[] for _ in range(bin_count)]
    for index, probability in enumerate(probabilities):
        bucket_index = min(bin_count - 1, int(probability * bin_count))
        raw[bucket_index].append(index)
    output: list[dict[str, Any]] = []
    for bucket_index, indices in enumerate(raw):
        lower = bucket_index / bin_count
        upper = (bucket_index + 1) / bin_count
        if indices:
            mean_probability = sum(probabilities[index] for index in indices) / len(indices)
            observed_rate = sum(labels[index] for index in indices) / len(indices)
            total_pnl = sum(pnls[index] for index in indices)
            expected_value = total_pnl / len(indices)
        else:
            mean_probability = 0.0
            observed_rate = 0.0
            total_pnl = 0.0
            expected_value = 0.0
        output.append(
            {
                "bucket_id": f"b{bucket_index:02d}",
                "lower_bound_inclusive": round(lower, 12),
                "upper_bound_inclusive": (
                    round(upper, 12) if bucket_index == bin_count - 1 else None
                ),
                "upper_bound_exclusive": round(upper, 12) if bucket_index < bin_count - 1 else None,
                "count": len(indices),
                "mean_predicted_probability": round(mean_probability, 12),
                "observed_positive_rate": round(observed_rate, 12),
                "expected_value": round(expected_value, 12),
                "total_pnl": round(total_pnl, 12),
            }
        )
    return output


def _binary_label(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("label must be binary") from exc
    if parsed not in {0, 1}:
        raise ValueError("label must be binary")
    return parsed


def _probability(value: Any, field: str) -> float:
    parsed = _finite_float(value, field)
    if parsed < 0.0 or parsed > 1.0:
        raise ValueError(f"{field} must be between 0 and 1")
    return parsed


def _finite_float(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{field} must be finite")
    return parsed
