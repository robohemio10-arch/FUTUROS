"""Parameter surface stability and rank-persistence analysis."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.stats import spearmanr

from .contracts import StrategyCandidate


def analyze_parameter_stability(
    candidates: Sequence[StrategyCandidate],
    candidate_scores: Mapping[str, float],
    fold_scores: Mapping[str, Sequence[float]],
    *,
    plateau_tolerance_fraction: float = 0.10,
) -> dict[str, Any]:
    by_family: dict[str, list[StrategyCandidate]] = defaultdict(list)
    for candidate in candidates:
        by_family[candidate.candidate_family].append(candidate)

    records: dict[str, dict[str, Any]] = {}
    for family, family_candidates in sorted(by_family.items()):
        axes = _parameter_axes(family_candidates)
        for candidate in family_candidates:
            candidate_id = candidate.candidate_id
            score = float(candidate_scores.get(candidate_id, 0.0))
            neighbors = _neighbors(candidate, family_candidates, axes)
            neighbor_scores = [float(candidate_scores.get(item.candidate_id, 0.0)) for item in neighbors]
            median_neighbor = float(np.median(neighbor_scores)) if neighbor_scores else score
            denominator = max(abs(score), 1e-12)
            local_drop = max(0.0, score - median_neighbor) / denominator
            plateau_count = sum(
                1 for neighbor_score in neighbor_scores if abs(score - neighbor_score) / denominator <= plateau_tolerance_fraction
            )
            boundary = _is_boundary(candidate, axes)
            rank_persistence = _rank_persistence(candidate_id, fold_scores, family_candidates)
            stability = max(
                0.0,
                min(
                    1.0,
                    0.45 * (1.0 - min(local_drop, 1.0))
                    + 0.30 * (plateau_count / max(1, len(neighbors)))
                    + 0.25 * max(0.0, rank_persistence),
                ),
            )
            isolated_spike = bool(neighbors and local_drop > 0.35 and plateau_count == 0)
            records[candidate_id] = {
                "candidate_id": candidate_id,
                "candidate_family": family,
                "neighbor_count": len(neighbors),
                "median_neighbor_score": median_neighbor,
                "local_performance_drop": local_drop,
                "plateau_width": plateau_count,
                "boundary_optimum": boundary,
                "rank_persistence": rank_persistence,
                "parameter_stability": stability,
                "isolated_spike": isolated_spike,
                "knife_edge_optimum": isolated_spike or (boundary and local_drop > 0.20),
            }
    return {
        "status": "ok" if records else "not_applicable",
        "candidate_stability": records,
        "family_count": len(by_family),
    }


def _parameter_axes(candidates: Sequence[StrategyCandidate]) -> dict[str, list[Any]]:
    axes: dict[str, list[Any]] = defaultdict(list)
    for candidate in candidates:
        for key, value in candidate.parameters.items():
            if value not in axes[key]:
                axes[key].append(value)
    for key, values in axes.items():
        try:
            axes[key] = sorted(values)
        except TypeError:
            axes[key] = sorted(values, key=lambda item: str(item))
    return dict(axes)


def _neighbors(
    candidate: StrategyCandidate,
    family_candidates: Sequence[StrategyCandidate],
    axes: Mapping[str, Sequence[Any]],
) -> list[StrategyCandidate]:
    neighbors: list[StrategyCandidate] = []
    for other in family_candidates:
        if other.candidate_id == candidate.candidate_id:
            continue
        differing = []
        adjacent = True
        for key, value in candidate.parameters.items():
            other_value = other.parameters.get(key)
            if other_value == value:
                continue
            differing.append(key)
            values = list(axes.get(key, ()))
            if value not in values or other_value not in values:
                adjacent = False
                break
            if abs(values.index(value) - values.index(other_value)) != 1:
                adjacent = False
                break
        if adjacent and len(differing) == 1:
            neighbors.append(other)
    return neighbors


def _is_boundary(candidate: StrategyCandidate, axes: Mapping[str, Sequence[Any]]) -> bool:
    for key, value in candidate.parameters.items():
        values = list(axes.get(key, ()))
        if len(values) > 1 and value in (values[0], values[-1]):
            return True
    return False


def _rank_persistence(
    candidate_id: str,
    fold_scores: Mapping[str, Sequence[float]],
    family_candidates: Sequence[StrategyCandidate],
) -> float:
    ids = [candidate.candidate_id for candidate in family_candidates]
    if len(ids) < 2:
        return 1.0
    max_folds = max((len(fold_scores.get(item, ())) for item in ids), default=0)
    if max_folds < 2:
        return 0.0
    rank_vectors: list[np.ndarray] = []
    for fold_number in range(max_folds):
        values = []
        for item in ids:
            scores = fold_scores.get(item, ())
            values.append(float(scores[fold_number]) if fold_number < len(scores) else 0.0)
        rank_vectors.append(np.asarray(values, dtype=float))
    correlations: list[float] = []
    for left in range(len(rank_vectors) - 1):
        for right in range(left + 1, len(rank_vectors)):
            result = spearmanr(rank_vectors[left], rank_vectors[right])
            correlation = float(result.statistic) if np.isfinite(result.statistic) else 0.0
            correlations.append(correlation)
    return float(np.mean(correlations)) if correlations else 0.0
