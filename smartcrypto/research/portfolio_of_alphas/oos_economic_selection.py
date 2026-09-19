"""OOS economic selection for the existing Portfolio of Alphas sleeves.

Research-only. The selector consumes the canonical Branch 14 Control x Treatment
scorecard plus explicit OOS sleeve observations. It never creates a strategy,
never fabricates missing sleeve economics, and never increases operational
capital or authority.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

SCHEMA_VERSION = "portfolio_of_alphas_oos_economic_selection_v1"
EVIDENCE_SCHEMA_VERSION = "portfolio_of_alphas_oos_sleeve_evidence_v1"
SCORECARD_SCHEMA_VERSION = "economic_control_treatment_scorecard_v1"

CANONICAL_SLEEVE_IDS = (
    "trend",
    "counter",
    "mean-reversion",
    "breakout",
    "microstructure",
    "relative-value",
    "regime-specialized",
)
MIN_OOS_OBSERVATIONS = 3
MAX_ABS_CORRELATION = 0.80

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "operational_authority": False,
    "promotion_allowed": False,
    "scaleout_allowed": False,
    "capital_increase_allowed": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "changes_risk": False,
    "changes_model": False,
    "changes_strategy": False,
    "changes_leverage": False,
    "changes_stake": False,
    "writes_runtime": False,
    "writes_sqlite": False,
    "writes_data": False,
}


class PortfolioAlphaSelectionError(RuntimeError):
    """Fail-closed error for Branch 15 economic selection."""


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PortfolioAlphaSelectionError(f"mapping_required:{field}")
    return value


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise PortfolioAlphaSelectionError(f"invalid_numeric:{field}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PortfolioAlphaSelectionError(f"invalid_numeric:{field}") from exc
    if not math.isfinite(number):
        raise PortfolioAlphaSelectionError(f"non_finite:{field}")
    return number


def _canonical_hash(payload: Any) -> str:
    rendered = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def _validate_scorecard(scorecard: Mapping[str, Any]) -> None:
    if scorecard.get("schema_version") != SCORECARD_SCHEMA_VERSION:
        raise PortfolioAlphaSelectionError("scorecard_schema_mismatch")
    if scorecard.get("status") != "ok":
        raise PortfolioAlphaSelectionError(
            f"scorecard_status_not_ok:{scorecard.get('status')}"
        )
    if scorecard.get("decision") != "SCORECARD_READY_RESEARCH_ONLY":
        raise PortfolioAlphaSelectionError("scorecard_not_research_ready")
    for key in ("paper_only", "shadow_only", "research_only"):
        if scorecard.get(key) is not True:
            raise PortfolioAlphaSelectionError(
                f"scorecard_safety_true_mismatch:{key}"
            )
    for key in (
        "operational_authority",
        "promotion_allowed",
        "sends_orders",
        "exchange_private_access",
        "changes_risk",
        "writes_runtime",
        "writes_data",
    ):
        if scorecard.get(key) is not False:
            raise PortfolioAlphaSelectionError(
                f"scorecard_safety_false_mismatch:{key}"
            )
    source = _mapping(
        scorecard.get("source_of_financial_truth"),
        "scorecard.source_of_financial_truth",
    )
    if source.get("new_financial_source_created") is not False:
        raise PortfolioAlphaSelectionError("scorecard_financial_truth_diverged")


def _scorecard_index(scorecard: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = scorecard.get("scorecards")
    if not isinstance(rows, list):
        raise PortfolioAlphaSelectionError("scorecard_rows_missing")
    indexed: dict[str, Mapping[str, Any]] = {}
    for raw in rows:
        row = _mapping(raw, "scorecard.row")
        experiment_id = str(row.get("experiment_id") or "").strip()
        if not experiment_id:
            raise PortfolioAlphaSelectionError("scorecard_experiment_id_missing")
        if experiment_id in indexed:
            raise PortfolioAlphaSelectionError(
                f"duplicate_scorecard_experiment:{experiment_id}"
            )
        decision = str(row.get("research_decision") or "").strip()
        if decision not in {
            "COLLECT/INSUFFICIENT",
            "DISCARD",
            "RECALIBRATE",
            "CANDIDATE",
        }:
            raise PortfolioAlphaSelectionError(
                f"scorecard_research_decision_invalid:{experiment_id}:{decision}"
            )
        if row.get("operational_authority") is not False:
            raise PortfolioAlphaSelectionError(
                f"scorecard_row_operational_authority:{experiment_id}"
            )
        if row.get("promotion_allowed") is not False:
            raise PortfolioAlphaSelectionError(
                f"scorecard_row_promotion_allowed:{experiment_id}"
            )
        blockers = row.get("decision_blockers")
        if decision == "CANDIDATE" and blockers not in ([], ()):
            raise PortfolioAlphaSelectionError(
                f"candidate_scorecard_has_blockers:{experiment_id}"
            )
        indexed[experiment_id] = row
    return indexed


def _validate_evidence(
    evidence: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if evidence.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        raise PortfolioAlphaSelectionError("sleeve_evidence_schema_mismatch")
    raw_sleeves = evidence.get("sleeves")
    if not isinstance(raw_sleeves, list):
        raise PortfolioAlphaSelectionError("sleeve_evidence_list_missing")

    normalized: list[dict[str, Any]] = []
    seen_sleeves: set[str] = set()
    seen_strategy_ids: set[str] = set()
    for raw in raw_sleeves:
        sleeve = _mapping(raw, "sleeve")
        sleeve_id = str(sleeve.get("sleeve_id") or "").strip()
        if sleeve_id not in CANONICAL_SLEEVE_IDS:
            raise PortfolioAlphaSelectionError(
                f"unsupported_or_noncanonical_sleeve:{sleeve_id}"
            )
        if sleeve_id in seen_sleeves:
            raise PortfolioAlphaSelectionError(f"duplicate_sleeve:{sleeve_id}")
        seen_sleeves.add(sleeve_id)

        hypothesis = str(sleeve.get("hypothesis") or "").strip()
        if len(hypothesis) < 8:
            raise PortfolioAlphaSelectionError(
                f"sleeve_hypothesis_missing_or_too_short:{sleeve_id}"
            )

        source_experiment_id = str(
            sleeve.get("source_experiment_id") or ""
        ).strip()
        if not source_experiment_id:
            raise PortfolioAlphaSelectionError(
                f"sleeve_source_experiment_missing:{sleeve_id}"
            )

        strategy_ids_raw = sleeve.get("strategy_ids")
        if not isinstance(strategy_ids_raw, list) or not strategy_ids_raw:
            raise PortfolioAlphaSelectionError(
                f"sleeve_strategy_ids_missing:{sleeve_id}"
            )
        strategy_ids = tuple(sorted(str(value).strip() for value in strategy_ids_raw))
        if any(not value for value in strategy_ids):
            raise PortfolioAlphaSelectionError(
                f"sleeve_strategy_id_empty:{sleeve_id}"
            )
        if len(strategy_ids) != len(set(strategy_ids)):
            raise PortfolioAlphaSelectionError(
                f"duplicate_strategy_within_sleeve:{sleeve_id}"
            )
        overlap = seen_strategy_ids.intersection(strategy_ids)
        if overlap:
            raise PortfolioAlphaSelectionError(
                "strategy_assigned_to_multiple_sleeves:" + ",".join(sorted(overlap))
            )
        seen_strategy_ids.update(strategy_ids)

        capacity_usdt = _finite(
            sleeve.get("capacity_usdt"),
            f"{sleeve_id}.capacity_usdt",
        )
        if capacity_usdt <= 0.0:
            raise PortfolioAlphaSelectionError(
                f"sleeve_capacity_not_positive:{sleeve_id}"
            )

        observations_raw = sleeve.get("observations")
        if not isinstance(observations_raw, list):
            raise PortfolioAlphaSelectionError(
                f"sleeve_observations_missing:{sleeve_id}"
            )
        observations: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for raw_observation in observations_raw:
            observation = _mapping(raw_observation, f"{sleeve_id}.observation")
            oos_key = str(observation.get("oos_key") or "").strip()
            if not oos_key:
                raise PortfolioAlphaSelectionError(
                    f"sleeve_oos_key_missing:{sleeve_id}"
                )
            if oos_key in seen_keys:
                raise PortfolioAlphaSelectionError(
                    f"duplicate_sleeve_oos_key:{sleeve_id}:{oos_key}"
                )
            seen_keys.add(oos_key)
            capital_hours = _finite(
                observation.get("capital_hours"),
                f"{sleeve_id}.{oos_key}.capital_hours",
            )
            if capital_hours <= 0.0:
                raise PortfolioAlphaSelectionError(
                    f"sleeve_capital_hours_not_positive:{sleeve_id}:{oos_key}"
                )
            observations.append(
                {
                    "oos_key": oos_key,
                    "net_pnl": _finite(
                        observation.get("net_pnl"),
                        f"{sleeve_id}.{oos_key}.net_pnl",
                    ),
                    "capital_hours": capital_hours,
                }
            )
        observations.sort(key=lambda item: item["oos_key"])
        normalized.append(
            {
                "sleeve_id": sleeve_id,
                "hypothesis": hypothesis,
                "source_experiment_id": source_experiment_id,
                "strategy_ids": list(strategy_ids),
                "capacity_usdt": capacity_usdt,
                "observations": observations,
            }
        )

    normalized.sort(key=lambda item: item["sleeve_id"])
    return normalized


def _series(sleeve: Mapping[str, Any]) -> dict[str, float]:
    return {
        str(item["oos_key"]): float(item["net_pnl"])
        for item in sleeve["observations"]
    }


def _metrics_from_series(
    pnl_by_key: Mapping[str, float],
    capital_hours_total: float,
) -> dict[str, Any]:
    values = [float(pnl_by_key[key]) for key in sorted(pnl_by_key)]
    if not values:
        return {
            "observation_count": 0,
            "net_pnl": 0.0,
            "expectancy": None,
            "profit_factor": None,
            "max_drawdown": None,
            "capital_hours_total": 0.0,
            "net_pnl_per_capital_hour": None,
        }
    positive = sum(value for value in values if value > 0.0)
    negative = abs(sum(value for value in values if value < 0.0))
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return {
        "observation_count": len(values),
        "net_pnl": float(sum(values)),
        "expectancy": float(sum(values) / len(values)),
        "profit_factor": float(positive / negative) if negative > 0.0 else None,
        "max_drawdown": float(max_drawdown),
        "capital_hours_total": float(capital_hours_total),
        "net_pnl_per_capital_hour": (
            float(sum(values) / capital_hours_total)
            if capital_hours_total > 0.0
            else None
        ),
    }


def _sleeve_metrics(sleeve: Mapping[str, Any]) -> dict[str, Any]:
    pnl_by_key = _series(sleeve)
    capital_hours = sum(
        float(item["capital_hours"]) for item in sleeve["observations"]
    )
    return _metrics_from_series(pnl_by_key, capital_hours)


def _pairwise_correlation(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> dict[str, Any]:
    left_series = _series(left)
    right_series = _series(right)
    common = sorted(set(left_series).intersection(right_series))
    if len(common) < MIN_OOS_OBSERVATIONS:
        return {
            "available": False,
            "correlation": None,
            "common_observation_count": len(common),
            "reason": "minimum_common_oos_observations_not_met",
        }
    left_values = np.asarray([left_series[key] for key in common], dtype=float)
    right_values = np.asarray([right_series[key] for key in common], dtype=float)
    if np.std(left_values) <= 1e-15 or np.std(right_values) <= 1e-15:
        return {
            "available": False,
            "correlation": None,
            "common_observation_count": len(common),
            "reason": "zero_variance_prevents_correlation",
        }
    correlation = float(np.corrcoef(left_values, right_values)[0, 1])
    if not math.isfinite(correlation):
        raise PortfolioAlphaSelectionError("pairwise_correlation_non_finite")
    return {
        "available": True,
        "correlation": correlation,
        "common_observation_count": len(common),
        "reason": "ok",
    }


def _combine_series(sleeves: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    combined: dict[str, float] = {}
    for sleeve in sleeves:
        for key, value in _series(sleeve).items():
            combined[key] = combined.get(key, 0.0) + value
    return combined


def _portfolio_metrics(sleeves: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    capital_hours = sum(
        float(item["capital_hours"])
        for sleeve in sleeves
        for item in sleeve["observations"]
    )
    metrics = _metrics_from_series(_combine_series(sleeves), capital_hours)
    total_hours = float(metrics["capital_hours_total"])
    concentration: list[dict[str, Any]] = []
    for sleeve in sorted(sleeves, key=lambda item: item["sleeve_id"]):
        sleeve_hours = sum(
            float(item["capital_hours"]) for item in sleeve["observations"]
        )
        concentration.append(
            {
                "sleeve_id": sleeve["sleeve_id"],
                "capital_hours": sleeve_hours,
                "capital_hour_concentration": (
                    sleeve_hours / total_hours if total_hours > 0.0 else None
                ),
                "declared_capacity_usdt": sleeve["capacity_usdt"],
            }
        )
    metrics["concentration"] = concentration
    metrics["max_capital_hour_concentration"] = (
        max(
            float(item["capital_hour_concentration"])
            for item in concentration
            if item["capital_hour_concentration"] is not None
        )
        if concentration
        else None
    )
    metrics["declared_capacity_sum_usdt"] = float(
        sum(float(sleeve["capacity_usdt"]) for sleeve in sleeves)
    )
    metrics["capacity_used_for_scaleout"] = False
    return metrics


def _standalone_edge(metrics: Mapping[str, Any]) -> tuple[bool, list[str]]:
    blockers: list[str] = []
    if int(metrics["observation_count"]) < MIN_OOS_OBSERVATIONS:
        blockers.append("minimum_oos_observations_not_met")
    if float(metrics["net_pnl"]) <= 0.0:
        blockers.append("standalone_net_pnl_not_positive")
    expectancy = metrics.get("expectancy")
    if expectancy is None or float(expectancy) <= 0.0:
        blockers.append("standalone_expectancy_not_positive")
    pf = metrics.get("profit_factor")
    if pf is None or float(pf) <= 1.0:
        blockers.append("standalone_profit_factor_not_above_one")
    efficiency = metrics.get("net_pnl_per_capital_hour")
    if efficiency is None or float(efficiency) <= 0.0:
        blockers.append("standalone_capital_hour_edge_not_positive")
    return not blockers, blockers


def _missing_inventory() -> list[dict[str, Any]]:
    return [
        {
            "sleeve_id": sleeve_id,
            "evidence_status": "missing",
            "selected": False,
            "exclusion_reasons": ["oos_sleeve_evidence_not_materialized"],
        }
        for sleeve_id in CANONICAL_SLEEVE_IDS
    ]


def build_portfolio_of_alphas_oos_economic_selection_from_scorecard(
    *,
    scorecard_report: Mapping[str, Any],
    sleeve_evidence: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Select only existing sleeves with positive, non-redundant OOS evidence."""

    _validate_scorecard(scorecard_report)
    scorecards = _scorecard_index(scorecard_report)

    if sleeve_evidence is None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "status": "waiting",
            "reason": "existing_sleeve_oos_evidence_not_materialized",
            "decision": "WAITING_FOR_EXISTING_SLEEVE_OOS_EVIDENCE",
            "source_scorecard_evidence_sha256": scorecard_report.get(
                "scorecard_evidence_sha256"
            ),
            "canonical_sleeves": list(CANONICAL_SLEEVE_IDS),
            "sleeve_count": 0,
            "selected_sleeve_count": 0,
            "selected_sleeves": [],
            "sleeve_inventory": _missing_inventory(),
            "correlation_matrix": [],
            "marginal_contributions": [],
            "portfolio_metrics": None,
            "selection_policy": {
                "requires_source_scorecard_candidate": True,
                "requires_positive_standalone_oos_edge": True,
                "requires_positive_marginal_net_pnl": True,
                "max_abs_correlation": MAX_ABS_CORRELATION,
                "missing_correlation_policy": "BLOCK",
                "creates_new_strategy": False,
                "increases_operational_capacity": False,
            },
            **SAFETY_FLAGS,
            "safety_flags": dict(SAFETY_FLAGS),
        }
        payload["selection_evidence_sha256"] = _canonical_hash(payload)
        return payload

    sleeves = _validate_evidence(sleeve_evidence)
    evidence_by_id = {item["sleeve_id"]: item for item in sleeves}
    metrics_by_id = {
        item["sleeve_id"]: _sleeve_metrics(item) for item in sleeves
    }

    correlation_matrix: list[dict[str, Any]] = []
    for left_index, left in enumerate(sleeves):
        for right in sleeves[left_index + 1 :]:
            correlation_matrix.append(
                {
                    "left_sleeve_id": left["sleeve_id"],
                    "right_sleeve_id": right["sleeve_id"],
                    **_pairwise_correlation(left, right),
                }
            )

    eligible: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    for sleeve in sleeves:
        sleeve_id = sleeve["sleeve_id"]
        metrics = metrics_by_id[sleeve_id]
        reasons: list[str] = []
        source_id = sleeve["source_experiment_id"]
        source = scorecards.get(source_id)
        source_decision = None
        if source is None:
            reasons.append("source_scorecard_experiment_unavailable")
        else:
            source_decision = str(source.get("research_decision"))
            if source_decision != "CANDIDATE":
                reasons.append(f"source_scorecard_not_candidate:{source_decision}")

        edge_ok, edge_blockers = _standalone_edge(metrics)
        if not edge_ok:
            reasons.extend(edge_blockers)

        row = {
            "sleeve_id": sleeve_id,
            "hypothesis": sleeve["hypothesis"],
            "strategy_ids": sleeve["strategy_ids"],
            "source_experiment_id": source_id,
            "source_research_decision": source_decision,
            "standalone_metrics": metrics,
            "declared_capacity_usdt": sleeve["capacity_usdt"],
            "evidence_status": "eligible" if not reasons else "excluded",
            "selected": False,
            "exclusion_reasons": sorted(set(reasons)),
        }
        inventory.append(row)
        if not reasons:
            eligible.append(sleeve)

    eligible.sort(
        key=lambda sleeve: (
            -float(
                metrics_by_id[sleeve["sleeve_id"]][
                    "net_pnl_per_capital_hour"
                ]
            ),
            -float(metrics_by_id[sleeve["sleeve_id"]]["net_pnl"]),
            sleeve["sleeve_id"],
        )
    )

    selected: list[dict[str, Any]] = []
    marginal_contributions: list[dict[str, Any]] = []
    inventory_by_id = {item["sleeve_id"]: item for item in inventory}

    for sleeve in eligible:
        sleeve_id = sleeve["sleeve_id"]
        redundancy_reasons: list[str] = []
        pairwise_with_selected: list[dict[str, Any]] = []
        for existing in selected:
            pair = _pairwise_correlation(sleeve, existing)
            pairwise_with_selected.append(
                {
                    "other_sleeve_id": existing["sleeve_id"],
                    **pair,
                }
            )
            if not pair["available"]:
                redundancy_reasons.append(
                    f"correlation_unavailable:{existing['sleeve_id']}"
                )
            elif abs(float(pair["correlation"])) > MAX_ABS_CORRELATION:
                redundancy_reasons.append(
                    f"redundant_correlation:{existing['sleeve_id']}"
                )

        before = _portfolio_metrics(selected)
        trial = [*selected, sleeve]
        after = _portfolio_metrics(trial)
        marginal_net_pnl = float(after["net_pnl"] - before["net_pnl"])
        if marginal_net_pnl <= 0.0:
            redundancy_reasons.append("marginal_net_pnl_not_positive")

        selected_now = not redundancy_reasons
        marginal_contributions.append(
            {
                "sleeve_id": sleeve_id,
                "selected": selected_now,
                "marginal_net_pnl": marginal_net_pnl,
                "portfolio_profit_factor_before": before["profit_factor"],
                "portfolio_profit_factor_after": after["profit_factor"],
                "portfolio_max_drawdown_before": before["max_drawdown"],
                "portfolio_max_drawdown_after": after["max_drawdown"],
                "portfolio_net_pnl_per_capital_hour_before": before[
                    "net_pnl_per_capital_hour"
                ],
                "portfolio_net_pnl_per_capital_hour_after": after[
                    "net_pnl_per_capital_hour"
                ],
                "pairwise_with_selected": pairwise_with_selected,
                "reasons": sorted(set(redundancy_reasons)),
            }
        )
        if selected_now:
            selected.append(sleeve)
            inventory_by_id[sleeve_id]["selected"] = True
            inventory_by_id[sleeve_id]["evidence_status"] = "selected"
        else:
            inventory_by_id[sleeve_id]["evidence_status"] = "excluded"
            inventory_by_id[sleeve_id]["exclusion_reasons"] = sorted(
                set(
                    inventory_by_id[sleeve_id]["exclusion_reasons"]
                    + redundancy_reasons
                )
            )

    for sleeve_id in CANONICAL_SLEEVE_IDS:
        if sleeve_id not in evidence_by_id:
            inventory.append(
                {
                    "sleeve_id": sleeve_id,
                    "evidence_status": "missing",
                    "selected": False,
                    "exclusion_reasons": [
                        "oos_sleeve_evidence_not_materialized"
                    ],
                }
            )
    inventory.sort(key=lambda item: item["sleeve_id"])

    portfolio_metrics = _portfolio_metrics(selected) if selected else None
    selected_ids = [item["sleeve_id"] for item in selected]
    if selected_ids:
        decision = "ECONOMICALLY_DEFENSIBLE_SLEEVES_SELECTED_RESEARCH_ONLY"
        reason = "positive_oos_edge_marginal_contribution_and_redundancy_gates_passed"
    else:
        decision = "NO_ECONOMICALLY_DEFENSIBLE_SLEEVE_CURRENT_EVIDENCE"
        reason = "all_materialized_sleeves_excluded_by_oos_economic_gates"

    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "reason": reason,
        "decision": decision,
        "source_scorecard_evidence_sha256": scorecard_report.get(
            "scorecard_evidence_sha256"
        ),
        "canonical_sleeves": list(CANONICAL_SLEEVE_IDS),
        "sleeve_count": len(sleeves),
        "selected_sleeve_count": len(selected_ids),
        "selected_sleeves": selected_ids,
        "sleeve_inventory": inventory,
        "correlation_matrix": correlation_matrix,
        "marginal_contributions": marginal_contributions,
        "portfolio_metrics": portfolio_metrics,
        "selection_policy": {
            "requires_source_scorecard_candidate": True,
            "requires_positive_standalone_oos_edge": True,
            "requires_positive_marginal_net_pnl": True,
            "max_abs_correlation": MAX_ABS_CORRELATION,
            "missing_correlation_policy": "BLOCK",
            "ranking_metric": "net_pnl_per_capital_hour_then_net_pnl",
            "creates_new_strategy": False,
            "increases_operational_capacity": False,
            "declared_capacity_used_for_scaleout": False,
        },
        **SAFETY_FLAGS,
        "safety_flags": dict(SAFETY_FLAGS),
    }
    payload["selection_evidence_sha256"] = _canonical_hash(payload)
    json.dumps(payload, sort_keys=True, allow_nan=False, default=str)
    return payload


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PortfolioAlphaSelectionError(
            f"invalid_sleeve_evidence_json:{path}"
        ) from exc
    if not isinstance(payload, dict):
        raise PortfolioAlphaSelectionError("sleeve_evidence_json_object_required")
    return payload


def build_portfolio_of_alphas_oos_economic_selection_v1(
    *,
    project_root: str | Path,
    master_path: str | Path,
    dataset_path: str | Path,
    feature_contract_path: str | Path,
    dataset_manifest_path: str | Path,
    split_manifest_path: str | Path,
    shadow_evidence_path: str | Path | None = None,
    sleeve_evidence_path: str | Path | None = None,
) -> dict[str, Any]:
    """Rebuild Branch 14 scorecard and run Branch 15 sleeve selection."""

    try:
        from smartcrypto.research.aibot_parity.economic_control_treatment_scorecard import (
            build_economic_control_treatment_scorecard_v1,
        )

        scorecard = build_economic_control_treatment_scorecard_v1(
            project_root=project_root,
            master_path=master_path,
            dataset_path=dataset_path,
            feature_contract_path=feature_contract_path,
            dataset_manifest_path=dataset_manifest_path,
            split_manifest_path=split_manifest_path,
            shadow_evidence_path=shadow_evidence_path,
        )
        evidence = None
        if sleeve_evidence_path is not None:
            candidate = Path(sleeve_evidence_path)
            path = candidate if candidate.is_absolute() else Path(project_root) / candidate
            path = path.resolve()
            if not path.is_file() or path.is_symlink():
                raise PortfolioAlphaSelectionError(
                    f"sleeve_evidence_path_invalid:{path}"
                )
            evidence = _read_json(path)

        return build_portfolio_of_alphas_oos_economic_selection_from_scorecard(
            scorecard_report=scorecard,
            sleeve_evidence=evidence,
        )
    except (
        PortfolioAlphaSelectionError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        ImportError,
    ) as exc:
        reason = (
            str(exc)
            if isinstance(exc, PortfolioAlphaSelectionError)
            else f"portfolio_alpha_selection_failed:{type(exc).__name__}"
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "blocked",
            "reason": reason,
            "decision": "PORTFOLIO_ALPHA_SELECTION_BLOCKED",
            **SAFETY_FLAGS,
            "safety_flags": dict(SAFETY_FLAGS),
        }


__all__ = [
    "CANONICAL_SLEEVE_IDS",
    "EVIDENCE_SCHEMA_VERSION",
    "MAX_ABS_CORRELATION",
    "MIN_OOS_OBSERVATIONS",
    "PortfolioAlphaSelectionError",
    "SCHEMA_VERSION",
    "build_portfolio_of_alphas_oos_economic_selection_from_scorecard",
    "build_portfolio_of_alphas_oos_economic_selection_v1",
]
