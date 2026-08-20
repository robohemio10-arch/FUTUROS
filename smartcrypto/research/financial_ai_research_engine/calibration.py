"""OOS financial calibration and economic ranking diagnostics."""

from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy.stats import spearmanr
from sklearn.metrics import mean_absolute_error, mean_squared_error, roc_auc_score

from smartcrypto.analysis.paper_financial_performance import compute_financial_metrics


EXPECTANCY_CI_CONFIDENCE = 0.95
EXPECTANCY_CI_RESAMPLES = 2000
EXPECTANCY_CI_SEED = 20260819


def tie_aware_buckets(
    scores: Sequence[float],
    labels: Sequence[int],
    pnls: Sequence[float],
    *,
    requested_bucket_count: int = 10,
) -> list[dict[str, Any]]:
    """Group identical scores before assigning deterministic quantile buckets."""

    if not (len(scores) == len(labels) == len(pnls)):
        raise ValueError("calibration_vectors_must_have_equal_length")
    if requested_bucket_count <= 0:
        raise ValueError("requested_bucket_count_must_be_positive")
    if not scores:
        return []
    frame = pd.DataFrame(
        {
            "score": [_finite(value, "score") for value in scores],
            "label": [_binary(value) for value in labels],
            "pnl": [_finite(value, "pnl") for value in pnls],
        }
    ).sort_values("score", kind="mergesort")
    unique_count = int(frame["score"].nunique())
    effective_count = min(requested_bucket_count, unique_count)
    if effective_count == 1:
        frame["bucket"] = 0
    else:
        unique_scores = frame[["score"]].drop_duplicates().reset_index(drop=True)
        unique_scores["bucket"] = np.floor(
            np.arange(unique_count, dtype=float) * effective_count / unique_count
        ).astype(int)
        mapping = dict(zip(unique_scores["score"], unique_scores["bucket"], strict=True))
        frame["bucket"] = frame["score"].map(mapping)
    output: list[dict[str, Any]] = []
    for bucket_id, group in frame.groupby("bucket", sort=True):
        output.append(
            {
                "bucket_id": f"b{int(bucket_id):02d}",
                "count": int(len(group)),
                "minimum_score": float(group["score"].min()),
                "maximum_score": float(group["score"].max()),
                "mean_predicted_probability": float(group["score"].mean()),
                "observed_positive_rate": float(group["label"].mean()),
                "realized_net_pnl_usdt_mean": float(group["pnl"].mean()),
                "realized_net_pnl_usdt_total": float(group["pnl"].sum()),
            }
        )
    return output


def financial_probability_metrics(
    probabilities: Sequence[float],
    labels: Sequence[int],
    pnls: Sequence[float],
    *,
    requested_bucket_count: int = 10,
) -> dict[str, Any]:
    if not probabilities:
        return _empty_probability_metrics("INSUFFICIENT_SAMPLE")
    checked = [_probability(value) for value in probabilities]
    checked_labels = [_binary(value) for value in labels]
    checked_pnls = [_finite(value, "pnl") for value in pnls]
    if not (len(checked) == len(checked_labels) == len(checked_pnls)):
        raise ValueError("calibration_vectors_must_have_equal_length")
    buckets = tie_aware_buckets(
        checked,
        checked_labels,
        checked_pnls,
        requested_bucket_count=requested_bucket_count,
    )
    brier = float(np.mean((np.asarray(checked) - np.asarray(checked_labels)) ** 2))
    ece = float(
        sum(
            bucket["count"]
            / len(checked)
            * abs(
                bucket["mean_predicted_probability"]
                - bucket["observed_positive_rate"]
            )
            for bucket in buckets
        )
    )
    auc = None
    if len(set(checked_labels)) == 2:
        auc = float(roc_auc_score(checked_labels, checked))
    monotonicity = bucket_monotonicity(
        [bucket["observed_positive_rate"] for bucket in buckets]
    )
    return {
        "status": "ok" if auc is not None else "INSUFFICIENT_CLASS_VARIATION",
        "score_semantics": "FINANCIAL_WIN_PROBABILITY",
        "sample_count": len(checked),
        "auc": auc,
        "brier": brier,
        "ece": ece,
        "bucket_count": requested_bucket_count,
        "effective_bucket_count": len(buckets),
        "monotonicity": monotonicity,
        "buckets": buckets,
    }


def regression_metrics(
    predicted: Sequence[float],
    realized: Sequence[float],
) -> dict[str, Any]:
    if not predicted:
        return {
            "status": "INSUFFICIENT_SAMPLE",
            "sample_count": 0,
            "mae": None,
            "rmse": None,
            "spearman": None,
            "top_n": {},
            "ev_buckets": [],
            "monotonicity": "INSUFFICIENT_BUCKETS",
        }
    if len(predicted) != len(realized):
        raise ValueError("regression_vectors_must_have_equal_length")
    predictions = np.asarray([_finite(value, "predicted") for value in predicted])
    actual = np.asarray([_finite(value, "realized") for value in realized])
    correlation = (
        spearmanr(predictions, actual).statistic if len(predictions) > 1 else None
    )
    if correlation is not None and not math.isfinite(float(correlation)):
        correlation = None
    buckets = _regression_buckets(predictions, actual)
    return {
        "status": "ok",
        "sample_count": int(len(predictions)),
        "mae": float(mean_absolute_error(actual, predictions)),
        "rmse": float(mean_squared_error(actual, predictions) ** 0.5),
        "spearman": float(correlation) if correlation is not None else None,
        "top_n": {
            label: _top_fraction_metrics(predictions, actual, fraction)
            for label, fraction in (
                ("top_10_pct", 0.10),
                ("top_20_pct", 0.20),
                ("top_50_pct", 0.50),
            )
        },
        "ev_buckets": buckets,
        "monotonicity": bucket_monotonicity(
            [bucket["realized_net_pnl_usdt_mean"] for bucket in buckets]
        ),
    }


def dependence_aware_expectancy_interval(
    pnls: Sequence[float],
    *,
    confidence: float = EXPECTANCY_CI_CONFIDENCE,
    resamples: int = EXPECTANCY_CI_RESAMPLES,
    seed: int = EXPECTANCY_CI_SEED,
) -> dict[str, Any]:
    """Deterministic circular moving-block bootstrap for serially ordered OOS PnL.

    The method preserves local dependence inside contiguous blocks. It is a
    research diagnostic, not an IID confidence interval.
    """

    values = np.asarray([_finite(value, "pnl") for value in pnls], dtype=float)
    sample_count = int(len(values))
    if sample_count < 30:
        return {
            "expectancy_ci_lower": None,
            "expectancy_ci_upper": None,
            "expectancy_ci_confidence": confidence,
            "expectancy_ci_method": "CIRCULAR_MOVING_BLOCK_BOOTSTRAP",
            "expectancy_ci_status": "INSUFFICIENT_SAMPLE",
            "expectancy_ci_sample_count": sample_count,
            "expectancy_ci_block_length": None,
            "expectancy_ci_resamples": 0,
            "iid_assumption": False,
        }
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence_must_be_between_zero_and_one")
    if resamples < 200:
        raise ValueError("resamples_must_be_at_least_200")

    block_length = max(3, min(sample_count, int(round(sample_count ** (1.0 / 3.0)))))
    block_count = int(math.ceil(sample_count / block_length))
    rng = np.random.default_rng(seed)
    bootstrap_means: NDArray[np.float64] = np.empty(resamples, dtype=np.float64)
    offsets: NDArray[np.int64] = np.arange(block_length, dtype=np.int64)

    for bootstrap_index in range(resamples):
        starts = rng.integers(0, sample_count, size=block_count)
        indices = np.concatenate(
            [((start + offsets) % sample_count) for start in starts]
        )[:sample_count]
        bootstrap_means[bootstrap_index] = float(np.mean(values[indices]))

    alpha = 1.0 - confidence
    lower = float(np.quantile(bootstrap_means, alpha / 2.0))
    upper = float(np.quantile(bootstrap_means, 1.0 - alpha / 2.0))
    return {
        "expectancy_ci_lower": lower,
        "expectancy_ci_upper": upper,
        "expectancy_ci_confidence": confidence,
        "expectancy_ci_method": "CIRCULAR_MOVING_BLOCK_BOOTSTRAP",
        "expectancy_ci_status": "AVAILABLE",
        "expectancy_ci_sample_count": sample_count,
        "expectancy_ci_block_length": block_length,
        "expectancy_ci_resamples": resamples,
        "iid_assumption": False,
    }


def financial_metrics(pnls: Sequence[float]) -> dict[str, Any]:
    checked = [_finite(value, "pnl") for value in pnls]
    if not checked:
        return {
            "trade_count": 0,
            "net_pnl": 0.0,
            "expectancy": None,
            "profit_factor": None,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "win_rate": None,
            "max_drawdown": 0.0,
            **dependence_aware_expectancy_interval([]),
        }
    frame = pd.DataFrame({"__pnl": checked})
    metrics = compute_financial_metrics(frame)
    interval = dependence_aware_expectancy_interval(checked)
    return {
        "trade_count": int(metrics["trades"]),
        "net_pnl": float(metrics["total_pnl"]),
        "expectancy": metrics["expectancy"],
        "profit_factor": metrics["profit_factor"],
        "gross_profit": float(metrics["gross_profit"]),
        "gross_loss": float(metrics["gross_loss"]),
        "win_rate": metrics["win_rate"],
        "max_drawdown": metrics["max_drawdown"],
        **interval,
    }


def bucket_monotonicity(values: Sequence[float]) -> str:
    if len(values) < 3:
        return "INSUFFICIENT_BUCKETS"
    deltas = np.diff(np.asarray(values, dtype=float))
    if np.all(deltas >= -1e-12) and np.any(deltas > 1e-12):
        return "MONOTONIC_NON_DECREASING"
    return "NON_MONOTONIC"


def _regression_buckets(
    predicted: np.ndarray,
    realized: np.ndarray,
) -> list[dict[str, Any]]:
    unique_count = int(len(np.unique(predicted)))
    bucket_count = min(5, unique_count)
    if bucket_count < 2 or len(predicted) < 20:
        return []
    order = np.argsort(predicted, kind="mergesort")
    unique_scores = np.unique(predicted[order])
    score_bucket = {
        float(score): min(bucket_count - 1, int(index * bucket_count / unique_count))
        for index, score in enumerate(unique_scores)
    }
    rows: list[dict[str, Any]] = []
    for bucket_id in range(bucket_count):
        indices = [
            index
            for index in order
            if score_bucket[float(predicted[index])] == bucket_id
        ]
        if not indices:
            continue
        rows.append(
            {
                "bucket_id": f"q{bucket_id + 1}",
                "count": len(indices),
                "predicted_ev_mean": float(np.mean(predicted[indices])),
                "realized_net_pnl_usdt_mean": float(np.mean(realized[indices])),
            }
        )
    return rows


def _top_fraction_metrics(
    predicted: np.ndarray,
    realized: np.ndarray,
    fraction: float,
) -> dict[str, Any]:
    """Evaluate a Top-N fraction without splitting equal scores at the cutoff.

    The nominal Top-N count is computed from the requested fraction. The score
    at that rank becomes the cutoff, and every observation tied at that cutoff
    is included. This preserves ranking semantics and prevents arbitrary
    stable-sort order from changing financial metrics when model scores tie.
    """

    requested_count = max(1, int(math.ceil(len(predicted) * fraction)))
    order = np.argsort(-predicted, kind="mergesort")
    cutoff_score = float(predicted[order[requested_count - 1]])
    selected = np.flatnonzero(predicted >= cutoff_score)
    selected_count = int(len(selected))

    return {
        "fraction": fraction,
        "requested_count": requested_count,
        "selected_count": selected_count,
        "effective_fraction": float(selected_count / len(predicted)),
        "cutoff_score": cutoff_score,
        "ties_preserved": True,
        **financial_metrics(realized[selected].tolist()),
    }


def _empty_probability_metrics(reason: str) -> dict[str, Any]:
    return {
        "status": reason,
        "score_semantics": "FINANCIAL_WIN_PROBABILITY",
        "sample_count": 0,
        "auc": None,
        "brier": None,
        "ece": None,
        "bucket_count": 10,
        "effective_bucket_count": 0,
        "monotonicity": "INSUFFICIENT_BUCKETS",
        "buckets": [],
    }


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name}_must_be_finite")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}_must_be_finite") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{name}_must_be_finite")
    return parsed


def _probability(value: Any) -> float:
    parsed = _finite(value, "probability")
    if parsed < 0.0 or parsed > 1.0:
        raise ValueError("probability_must_be_between_zero_and_one")
    return parsed


def _binary(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("label_must_be_binary") from exc
    if parsed not in {0, 1}:
        raise ValueError("label_must_be_binary")
    return parsed
