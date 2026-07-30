"""Deterministic robustness, multiple-testing, CPCV/PBO, and risk analytics."""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Mapping, TypeAlias

import numpy as np
import pandas as pd
from numpy.typing import ArrayLike, NDArray
from scipy.stats import kurtosis, norm, rankdata, skew

from .contracts import RobustnessContract, TemporalSplitContract, stable_hash
from .metrics import drawdown_metrics
from .splits import build_cpcv_paths, purge_cpcv_path

EULER_GAMMA = 0.5772156649015329
FloatArray: TypeAlias = NDArray[np.float64]


@dataclass(frozen=True)
class MonteCarloResult:
    method: str
    seed: int
    simulation_count: int
    horizon: int
    metrics: Mapping[str, Any]
    distribution_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "seed": self.seed,
            "simulation_count": self.simulation_count,
            "horizon": self.horizon,
            "metrics": dict(self.metrics),
            "distribution_hash": self.distribution_hash,
        }


def run_monte_carlo_suite(
    returns: ArrayLike,
    *,
    contract: RobustnessContract,
    initial_capital: float = 1.0,
    cost_stress_per_trade: float = 0.0,
) -> dict[str, Any]:
    clean = _clean_returns(returns)
    if len(clean) < 2:
        return {
            "status": "blocked",
            "reason": "insufficient_returns",
            "methods": [],
            "risk_of_ruin": None,
            "worst_method": None,
        }
    methods: list[MonteCarloResult] = []
    for offset, method in enumerate(("trade_permutation", "iid_bootstrap", "block_bootstrap", "cost_stress")):
        stressed: FloatArray
        if method == "cost_stress":
            stressed = np.asarray(
                clean - float(cost_stress_per_trade), dtype=np.float64
            )
        else:
            stressed = clean.copy()
        paths = simulate_paths(
            stressed,
            method="iid_bootstrap" if method == "cost_stress" else method,
            simulations=contract.monte_carlo_simulations,
            horizon=len(clean),
            block_size=contract.block_bootstrap_size,
            seed=contract.seed + offset,
        )
        metrics = summarize_paths(
            paths,
            initial_capital=initial_capital,
            ruin_threshold_fraction=contract.ruin_threshold_fraction,
        )
        methods.append(
            MonteCarloResult(
                method=method,
                seed=contract.seed + offset,
                simulation_count=contract.monte_carlo_simulations,
                horizon=len(clean),
                metrics=metrics,
                distribution_hash=stable_hash(paths.round(12).tolist()),
            )
        )
    worst = max(methods, key=lambda item: float(item.metrics["risk_of_ruin"]))
    return {
        "status": "ok",
        "reason": "monte_carlo_suite_completed",
        "methods": [item.to_dict() for item in methods],
        "risk_of_ruin": float(worst.metrics["risk_of_ruin"]),
        "worst_method": worst.method,
        "maximum_drawdown_p95": max(float(item.metrics["maximum_drawdown_p95"]) for item in methods),
        "expected_shortfall_p05": min(float(item.metrics["expected_shortfall_p05"]) for item in methods),
    }


def simulate_paths(
    returns: ArrayLike,
    *,
    method: str,
    simulations: int,
    horizon: int,
    block_size: int,
    seed: int,
) -> FloatArray:
    clean = _clean_returns(returns)
    if clean.size == 0:
        raise ValueError("returns_empty")
    if simulations <= 0 or horizon <= 0:
        raise ValueError("simulations_and_horizon_must_be_positive")
    rng = np.random.default_rng(seed)
    if method == "trade_permutation":
        paths: FloatArray = np.empty((simulations, horizon), dtype=np.float64)
        for index in range(simulations):
            repeated: FloatArray = np.asarray(
                np.resize(clean, horizon), dtype=np.float64
            ).copy()
            rng.shuffle(repeated)
            paths[index] = repeated
        return paths
    if method == "iid_bootstrap":
        return np.asarray(
            rng.choice(clean, size=(simulations, horizon), replace=True),
            dtype=np.float64,
        )
    if method == "block_bootstrap":
        return _block_bootstrap(clean, simulations, horizon, block_size, rng)
    raise ValueError(f"unsupported_monte_carlo_method:{method}")


def summarize_paths(
    paths: FloatArray,
    *,
    initial_capital: float,
    ruin_threshold_fraction: float,
) -> dict[str, Any]:
    if paths.ndim != 2 or paths.size == 0:
        raise ValueError("paths_must_be_non_empty_matrix")
    terminal = paths.sum(axis=1)
    max_drawdowns = np.asarray(
        [drawdown_metrics(row.tolist())["maximum_drawdown"] for row in paths],
        dtype=float,
    )
    losing_streaks = np.asarray([_longest_losing_streak(row) for row in paths], dtype=float)
    profit_factors = np.asarray([_profit_factor(row) for row in paths], dtype=float)
    sharpes = np.asarray([_sharpe(row) for row in paths], dtype=float)
    equity = initial_capital + np.cumsum(paths, axis=1)
    ruin_level = initial_capital * (1.0 - ruin_threshold_fraction)
    ruined = (equity <= ruin_level).any(axis=1)
    losses = -terminal
    return {
        "terminal_pnl_percentile_01": float(np.quantile(terminal, 0.01)),
        "terminal_pnl_percentile_05": float(np.quantile(terminal, 0.05)),
        "terminal_pnl_median": float(np.median(terminal)),
        "terminal_pnl_percentile_95": float(np.quantile(terminal, 0.95)),
        "terminal_pnl_worst_case": float(terminal.min()),
        "maximum_drawdown_percentile_95": float(np.quantile(max_drawdowns, 0.95)),
        "maximum_drawdown_p95": float(np.quantile(max_drawdowns, 0.95)),
        "longest_losing_streak_p95": float(np.quantile(losing_streaks, 0.95)),
        "profit_factor_median": float(np.median(profit_factors)),
        "sharpe_median": float(np.median(sharpes)),
        "risk_of_ruin": float(ruined.mean()),
        "loss_threshold_probability": float((terminal < 0).mean()),
        "drawdown_threshold_probability": float((max_drawdowns >= initial_capital * ruin_threshold_fraction).mean()),
        "expected_shortfall_p05": expected_shortfall(terminal, 0.05),
        "loss_cvar_p95": conditional_value_at_risk(losses, 0.95),
    }


def cpcv_probability_of_backtest_overfitting(
    candidate_returns: Mapping[str, ArrayLike],
    *,
    group_count: int,
    test_group_count: int,
    frame: pd.DataFrame | None = None,
    split_contract: TemporalSplitContract | None = None,
) -> dict[str, Any]:
    """Estimate PBO through CPCV, optionally applying temporal purge and embargo."""

    if not candidate_returns:
        return _blocked_pbo("no_candidates")
    ids = sorted(candidate_returns)
    arrays = [_clean_returns(candidate_returns[candidate_id]) for candidate_id in ids]
    lengths = {len(array) for array in arrays}
    if len(lengths) != 1:
        return _blocked_pbo("candidate_return_lengths_differ")
    row_count = lengths.pop()
    if row_count < group_count or group_count < 3 or not 0 < test_group_count < group_count:
        return _blocked_pbo("invalid_cpcv_configuration")
    if frame is not None and len(frame) != row_count:
        return _blocked_pbo("cpcv_frame_length_mismatch")
    if (frame is None) != (split_contract is None):
        return _blocked_pbo("cpcv_frame_and_split_contract_must_be_supplied_together")

    matrix: FloatArray = np.asarray(np.vstack(arrays), dtype=np.float64)
    raw_paths: list[dict[str, Any]]
    if frame is None:
        groups = _contiguous_groups(row_count, group_count)
        raw_paths = []
        for path_number, test_groups in enumerate(
            combinations(range(group_count), test_group_count), start=1
        ):
            test_group_set = set(test_groups)
            raw_paths.append(
                {
                    "path_number": path_number,
                    "test_groups": list(test_groups),
                    "train_indices": [
                        index
                        for group_number, group in enumerate(groups)
                        if group_number not in test_group_set
                        for index in group
                    ],
                    "test_indices": [index for group in test_groups for index in groups[group]],
                    "purged_indices": [],
                    "embargoed_indices": [],
                }
            )
    else:
        if split_contract is None:
            return _blocked_pbo("cpcv_split_contract_missing")
        temporal = frame.copy().reset_index(drop=True)
        temporal["open_time_utc"] = pd.to_datetime(
            temporal["open_time_utc"], utc=True, errors="coerce"
        )
        temporal["close_time_utc"] = pd.to_datetime(
            temporal["close_time_utc"], utc=True, errors="coerce"
        )
        if temporal[["open_time_utc", "close_time_utc"]].isna().any().any():
            return _blocked_pbo("invalid_cpcv_temporal_values")
        raw_paths = [
            purge_cpcv_path(temporal, dict(path), split_contract)
            for path in build_cpcv_paths(row_count, group_count, test_group_count)
        ]

    records: list[dict[str, Any]] = []
    logits: list[float] = []
    blocked_path_count = 0
    for path in raw_paths:
        train_indices = np.asarray(path["train_indices"], dtype=int)
        test_indices = np.asarray(path["test_indices"], dtype=int)
        if train_indices.size < 2 or test_indices.size < 2:
            blocked_path_count += 1
            continue
        train_scores = np.asarray([_sharpe(row[train_indices]) for row in matrix], dtype=float)
        selected = int(np.argmax(train_scores))
        test_scores = np.asarray([_sharpe(row[test_indices]) for row in matrix], dtype=float)
        selected_test_score = float(test_scores[selected])
        ranks = rankdata(test_scores, method="average")
        percentile_rank = float((ranks[selected] - 0.5) / len(ids))
        percentile_rank = min(max(percentile_rank, 1e-9), 1.0 - 1e-9)
        logit_rank = float(math.log(percentile_rank / (1.0 - percentile_rank)))
        logits.append(logit_rank)
        records.append(
            {
                "path_number": int(path["path_number"]),
                "test_groups": list(path["test_groups"]),
                "selected_candidate_id": ids[selected],
                "selected_train_sharpe": float(train_scores[selected]),
                "selected_test_sharpe": selected_test_score,
                "test_percentile_rank": percentile_rank,
                "logit_rank": logit_rank,
                "train_row_count_after_purge": int(train_indices.size),
                "test_row_count": int(test_indices.size),
                "purged_row_count": len(path.get("purged_indices", ())),
                "embargoed_row_count": len(path.get("embargoed_indices", ())),
                "path_hash": path.get("path_hash"),
            }
        )
    if not records:
        result = _blocked_pbo("no_valid_cpcv_paths_after_purge_embargo")
        result["blocked_path_count"] = blocked_path_count
        return result
    pbo = float(np.mean(np.asarray(logits) <= 0.0))
    return {
        "status": "ok",
        "reason": "cpcv_pbo_completed_with_purge_embargo" if frame is not None else "cpcv_pbo_completed",
        "candidate_count": len(ids),
        "path_count": len(raw_paths),
        "valid_path_count": len(records),
        "blocked_path_count": blocked_path_count,
        "purge_applied": frame is not None,
        "embargo_applied": frame is not None and bool(split_contract and split_contract.embargo_seconds > 0),
        "pbo": pbo,
        "logit_rank_median": float(np.median(logits)),
        "paths": records,
        "result_hash": stable_hash(records),
    }


def deflated_sharpe_ratio(
    returns: ArrayLike,
    *,
    trial_count: int,
    annualization_factor: int,
) -> dict[str, Any]:
    clean = _clean_returns(returns)
    if clean.size < 3 or trial_count <= 0:
        return {
            "status": "blocked",
            "reason": "insufficient_sample_or_trials",
            "probability": None,
        }
    observed = _sharpe(clean, annualization_factor=annualization_factor)
    raw_sr = float(np.mean(clean) / np.std(clean, ddof=1))
    sample_skew = float(skew(clean, bias=False)) if clean.size > 2 else 0.0
    sample_kurtosis = float(kurtosis(clean, fisher=False, bias=False)) if clean.size > 3 else 3.0
    variance = max(
        1e-12,
        (1.0 - sample_skew * raw_sr + ((sample_kurtosis - 1.0) / 4.0) * raw_sr**2)
        / (clean.size - 1),
    )
    sr_std = math.sqrt(variance) * math.sqrt(annualization_factor)
    if trial_count == 1:
        expected_max = 0.0
    else:
        first = norm.ppf(1.0 - 1.0 / trial_count)
        second = norm.ppf(1.0 - 1.0 / (trial_count * math.e))
        expected_max = sr_std * ((1.0 - EULER_GAMMA) * first + EULER_GAMMA * second)
    probability = float(norm.cdf((observed - expected_max) / sr_std)) if sr_std > 0 else 0.0
    return {
        "status": "ok",
        "reason": "deflated_sharpe_completed",
        "observed_sharpe": observed,
        "estimated_variance": variance,
        "skew": sample_skew,
        "kurtosis": sample_kurtosis,
        "trial_count": trial_count,
        "expected_maximum_sharpe": expected_max,
        "probability": probability,
    }


def white_reality_check(
    candidate_returns: Mapping[str, ArrayLike],
    benchmark_returns: ArrayLike,
    *,
    simulations: int,
    block_size: int,
    seed: int,
) -> dict[str, Any]:
    ids = sorted(candidate_returns)
    benchmark = _clean_returns(benchmark_returns)
    if not ids or benchmark.size < 2:
        return {"status": "blocked", "reason": "insufficient_candidates_or_benchmark", "pvalue": None}
    arrays = [_clean_returns(candidate_returns[candidate_id]) for candidate_id in ids]
    if any(len(array) != len(benchmark) for array in arrays):
        return {"status": "blocked", "reason": "return_lengths_differ", "pvalue": None}
    excess = np.vstack([array - benchmark for array in arrays])
    observed_means = excess.mean(axis=1)
    observed_statistic = float(observed_means.max())
    centered = excess - observed_means[:, None]
    rng = np.random.default_rng(seed)
    bootstrap_stats: FloatArray = np.empty(simulations, dtype=np.float64)
    for simulation in range(simulations):
        indices = _block_indices(len(benchmark), block_size, rng)
        bootstrap_stats[simulation] = float(centered[:, indices].mean(axis=1).max())
    pvalue = float((1 + np.sum(bootstrap_stats >= observed_statistic)) / (simulations + 1))
    best = int(np.argmax(observed_means))
    return {
        "status": "ok",
        "reason": "white_reality_check_completed",
        "benchmark": "explicit_benchmark",
        "best_candidate_id": ids[best],
        "observed_statistic": observed_statistic,
        "pvalue": pvalue,
        "simulation_count": simulations,
        "seed": seed,
        "bootstrap_hash": stable_hash(bootstrap_stats.round(12).tolist()),
    }


def adjust_pvalues(pvalues: Mapping[str, float]) -> dict[str, dict[str, float]]:
    ids = sorted(pvalues)
    values = np.asarray([float(pvalues[item]) for item in ids], dtype=float)
    if np.any((values < 0.0) | (values > 1.0)):
        raise ValueError("pvalues_outside_unit_interval")
    count = len(ids)
    bonferroni = np.minimum(values * count, 1.0)

    order = np.argsort(values)
    holm: FloatArray = np.empty(count, dtype=np.float64)
    running = 0.0
    for rank, index in enumerate(order):
        adjusted = min(1.0, (count - rank) * values[index])
        running = max(running, adjusted)
        holm[index] = running

    bh: FloatArray = np.empty(count, dtype=np.float64)
    running_bh = 1.0
    for reverse_rank, index in enumerate(order[::-1], start=1):
        rank = count - reverse_rank + 1
        adjusted = min(1.0, values[index] * count / rank)
        running_bh = min(running_bh, adjusted)
        bh[index] = running_bh

    return {
        candidate_id: {
            "raw": float(values[index]),
            "bonferroni": float(bonferroni[index]),
            "holm": float(holm[index]),
            "benjamini_hochberg": float(bh[index]),
        }
        for index, candidate_id in enumerate(ids)
    }


def expected_shortfall(values: ArrayLike, quantile: float) -> float:
    array = np.asarray(values, dtype=float)
    threshold = np.quantile(array, quantile)
    tail = array[array <= threshold]
    return float(tail.mean()) if tail.size else float(threshold)


def conditional_value_at_risk(losses: ArrayLike, confidence: float) -> float:
    array = np.asarray(losses, dtype=float)
    threshold = np.quantile(array, confidence)
    tail = array[array >= threshold]
    return float(tail.mean()) if tail.size else float(threshold)


def _block_bootstrap(
    returns: FloatArray,
    simulations: int,
    horizon: int,
    block_size: int,
    rng: np.random.Generator,
) -> FloatArray:
    if block_size <= 0:
        raise ValueError("block_size_must_be_positive")
    paths: FloatArray = np.empty((simulations, horizon), dtype=np.float64)
    for simulation in range(simulations):
        indices = _block_indices(len(returns), block_size, rng)
        paths[simulation] = returns[indices[:horizon]]
    return paths


def _block_indices(
    length: int, block_size: int, rng: np.random.Generator
) -> NDArray[np.int64]:
    indices: list[int] = []
    while len(indices) < length:
        start = int(rng.integers(0, length))
        indices.extend((start + offset) % length for offset in range(block_size))
    return np.asarray(indices[:length], dtype=np.int64)


def _contiguous_groups(row_count: int, group_count: int) -> tuple[tuple[int, ...], ...]:
    base, remainder = divmod(row_count, group_count)
    groups: list[tuple[int, ...]] = []
    cursor = 0
    for group_number in range(group_count):
        size = base + (1 if group_number < remainder else 0)
        groups.append(tuple(range(cursor, cursor + size)))
        cursor += size
    return tuple(groups)


def _clean_returns(values: ArrayLike) -> FloatArray:
    array = np.asarray(values, dtype=np.float64).reshape(-1)
    return np.asarray(array[np.isfinite(array)], dtype=np.float64)


def _longest_losing_streak(values: ArrayLike) -> int:
    longest = 0
    current = 0
    for value in np.asarray(values, dtype=np.float64).reshape(-1):
        if float(value) < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _profit_factor(values: ArrayLike) -> float:
    array = np.asarray(values, dtype=float)
    positive = float(array[array > 0].sum())
    negative = float(-array[array < 0].sum())
    if negative == 0:
        return positive if positive > 0 else 0.0
    return positive / negative


def _sharpe(values: ArrayLike, *, annualization_factor: int = 1) -> float:
    array = np.asarray(values, dtype=float)
    if array.size < 2:
        return 0.0
    std = float(array.std(ddof=1))
    if std <= 0:
        return 0.0
    return float(array.mean() / std * math.sqrt(annualization_factor))


def _blocked_pbo(reason: str) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": reason,
        "candidate_count": 0,
        "path_count": 0,
        "valid_path_count": 0,
        "blocked_path_count": 0,
        "pbo": None,
        "paths": [],
    }
