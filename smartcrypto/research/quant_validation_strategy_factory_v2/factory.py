"""Deterministic research-only Strategy Factory candidate generation."""

from __future__ import annotations

from itertools import product
from typing import Any, Mapping, Sequence

from .contracts import StrategyCandidate, stable_hash

DEFAULT_FAMILIES: dict[str, dict[str, Sequence[Any]]] = {
    "baseline_no_change": {},
    "fixed_tp_sl": {
        "take_profit_bps": (10, 20, 30),
        "stop_loss_bps": (20, 40, 60),
    },
    "atr_stop": {
        "atr_period": (7, 14, 21),
        "atr_multiplier": (1.0, 1.5, 2.0),
    },
    "trailing_stop": {
        "activation_bps": (10, 20, 30),
        "trail_bps": (5, 10, 15),
    },
    "entry_threshold": {
        "entry_threshold": (0.45, 0.50, 0.55),
    },
    "holding_period": {
        "maximum_holding_minutes": (15, 30, 60),
    },
}


def generate_candidates(
    family_spaces: Mapping[str, Mapping[str, Sequence[Any]]] | None = None,
    *,
    strategy_version: str = "v2",
    include_families: Sequence[str] | None = None,
) -> tuple[StrategyCandidate, ...]:
    spaces = family_spaces or DEFAULT_FAMILIES
    selected = set(include_families or spaces.keys())
    candidates: list[StrategyCandidate] = []
    fingerprints: set[str] = set()
    for family in sorted(spaces):
        if family not in selected:
            continue
        parameter_space = spaces[family]
        keys = sorted(parameter_space)
        combinations = product(*(parameter_space[key] for key in keys)) if keys else [()]
        for values in combinations:
            parameters = {key: value for key, value in zip(keys, values, strict=True)}
            candidate = StrategyCandidate(
                candidate_family=family,
                strategy_version=strategy_version,
                parameters=parameters,
                baseline_control=family == "baseline_no_change",
                rationale=_rationale(family),
            )
            semantic_fingerprint = stable_hash(
                {
                    "family": family,
                    "parameters": parameters,
                    "baseline_control": candidate.baseline_control,
                }
            )
            if semantic_fingerprint in fingerprints:
                continue
            fingerprints.add(semantic_fingerprint)
            candidates.append(candidate)
    return tuple(candidates)


def candidate_registry_records(
    candidates: Sequence[StrategyCandidate],
    *,
    protocol_hash: str,
    dataset_hash: str,
    split_hash: str | None,
    cost_model_hash: str,
    commit_sha: str,
    decisions: Mapping[str, str],
    blockers: Mapping[str, Sequence[str]],
    created_at_utc: str,
) -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: item.candidate_id):
        payload = {
            "candidate_id": candidate.candidate_id,
            "candidate_hash": stable_hash(candidate.canonical_payload()),
            "candidate_family": candidate.candidate_family,
            "candidate_version": candidate.strategy_version,
            "parameter_hash": candidate.parameter_hash,
            "protocol_hash": protocol_hash,
            "dataset_hash": dataset_hash,
            "split_hash": split_hash,
            "cost_model_hash": cost_model_hash,
            "commit_sha": commit_sha,
            "decision": decisions.get(candidate.candidate_id, "REJECTED_INSUFFICIENT_SAMPLE"),
            "blockers": sorted(set(blockers.get(candidate.candidate_id, ()))),
            "operational_authority": False,
            "automatic_promotion_allowed": False,
            "writes_active_registry": False,
        }
        records.append(
            {
                **payload,
                "evaluation_hash": stable_hash(payload),
                "created_at_utc": created_at_utc,
            }
        )
    return tuple(records)


def parameter_space_hash(family_spaces: Mapping[str, Mapping[str, Sequence[Any]]] | None = None) -> str:
    spaces = family_spaces or DEFAULT_FAMILIES
    normalized = {
        family: {key: list(values) for key, values in sorted(space.items())}
        for family, space in sorted(spaces.items())
    }
    return stable_hash(normalized)


def _rationale(family: str) -> str:
    rationales = {
        "baseline_no_change": "Canonical no-change control.",
        "fixed_tp_sl": "Evaluate fixed take-profit and stop-loss surfaces.",
        "atr_stop": "Evaluate volatility-scaled protective exits.",
        "trailing_stop": "Evaluate path-dependent profit protection.",
        "entry_threshold": "Evaluate decision-threshold sensitivity.",
        "holding_period": "Evaluate maximum holding-period sensitivity.",
    }
    return rationales.get(family, "Versioned research-only strategy family.")
