"""Deterministic momentum arms and conservative profit-protection simulation."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from smartcrypto.research.paper_profit_maximization.metrics import (
    finite_or_none,
    prepare_profit_dataset,
    profit_metrics,
    sort_trades,
)

from .contracts import (
    PROTECTION_RETENTION_FRACTIONS,
    PROTECTION_TRIGGER_PCTS,
    RET1_THRESHOLD,
    RET12_THRESHOLD,
    TEMPORAL_TRAIN_RATIO,
)


ARM_CONTROL = "control_all_eligible"
ARM_RET12 = "momentum_ret12"
ARM_RET12_RET1 = "momentum_ret12_ret1"


def evaluate_momentum_protection_ab(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Evaluate fixed momentum arms and a no-write protection grid."""
    prepared, preparation = prepare_profit_dataset(frame)
    eligible = sort_trades(
        prepared.loc[prepared["profit_optimization_eligible"]].copy()
    )
    if eligible.empty:
        return prepared, {
            "status": "blocked",
            "reason": "no_profit_optimization_eligible_paper_trades",
            "preparation": preparation,
            "arm_results": [],
            "ranked_candidates": [],
            "best_candidate": None,
        }

    eligible = eligible.reset_index(drop=True)
    split = max(1, int(len(eligible) * TEMPORAL_TRAIN_RATIO))
    split = min(split, max(1, len(eligible) - 1))
    eligible["__ab_oos"] = False
    eligible.loc[eligible.index >= split, "__ab_oos"] = True

    masks = build_momentum_arm_masks(eligible)
    global_metrics = profit_metrics(eligible)
    global_oos = profit_metrics(eligible.loc[eligible["__ab_oos"]])
    arm_results: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []

    for arm_id, mask in masks.items():
        result, arm_candidates = _evaluate_arm(
            eligible,
            mask,
            arm_id=arm_id,
            global_metrics=global_metrics,
            global_oos=global_oos,
        )
        arm_results.append(result)
        candidates.extend(arm_candidates)

    ranked = rank_ab_candidates(candidates)
    best = next(
        (
            item
            for item in ranked
            if item.get("decision") == "PROMOVER_PARA_PAPER_AB"
        ),
        None,
    )
    output = prepared.copy()
    output["ab_arm_ret12_eligible"] = False
    output["ab_arm_ret12_ret1_eligible"] = False
    eligible_ids = eligible["stable_trade_id"].astype("string")
    ret12_ids = set(
        eligible.loc[masks[ARM_RET12], "stable_trade_id"].astype("string")
    )
    combo_ids = set(
        eligible.loc[masks[ARM_RET12_RET1], "stable_trade_id"].astype("string")
    )
    stable = output["stable_trade_id"].astype("string")
    output["ab_arm_ret12_eligible"] = stable.isin(ret12_ids)
    output["ab_arm_ret12_ret1_eligible"] = stable.isin(combo_ids)

    return output, {
        "status": "ok",
        "reason": "paper_profit_momentum_protection_ab_completed",
        "baseline_paper_metrics": global_metrics,
        "baseline_oos_metrics": global_oos,
        "eligible_trade_count": int(len(eligible)),
        "train_trade_count": int((~eligible["__ab_oos"]).sum()),
        "oos_trade_count": int(eligible["__ab_oos"].sum()),
        "ret12_threshold": RET12_THRESHOLD,
        "ret1_threshold": RET1_THRESHOLD,
        "arm_results": arm_results,
        "protection_candidate_count": int(
            sum(item.get("protection_id") != "none" for item in candidates)
        ),
        "ranked_candidates": ranked[:20],
        "best_candidate": best,
        "positive_robust_candidate_found": best is not None,
        "preparation": preparation,
        "split_policy": {
            "kind": "chronological_70_30",
            "train_ratio": TEMPORAL_TRAIN_RATIO,
            "split_universe": "all_profit_optimization_eligible_paper_trades",
        },
        "protection_model": {
            "optimistic_bound": (
                "floor applied when final realized net pnl proves the protected "
                "floor was crossed after favorable excursion"
            ),
            "pessimistic_bound": (
                "floor applied whenever post-MFE retracement can reach the floor, "
                "including potential early exits of winners"
            ),
            "decision_basis": "pessimistic_bound",
            "cost_basis": "observed_fees_plus_funding",
        },
    }


def build_momentum_arm_masks(frame: pd.DataFrame) -> dict[str, pd.Series]:
    """Build the three fixed A/B arms authorized for this branch."""
    ret12 = pd.to_numeric(frame.get("entry_return_12"), errors="coerce")
    ret1 = pd.to_numeric(frame.get("entry_return_1"), errors="coerce")
    control = pd.Series(True, index=frame.index, dtype=bool)
    return {
        ARM_CONTROL: control,
        ARM_RET12: ret12.ge(RET12_THRESHOLD).fillna(False),
        ARM_RET12_RET1: (
            ret12.ge(RET12_THRESHOLD) & ret1.ge(RET1_THRESHOLD)
        ).fillna(False),
    }


def rank_ab_candidates(
    candidates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Rank by robust OOS profit, then full-period profit and drawdown."""
    return sorted(
        [dict(item) for item in candidates],
        key=lambda item: (
            item.get("decision") != "PROMOVER_PARA_PAPER_AB",
            -_sort_float(item.get("robust_oos_delta_vs_global_baseline")),
            -_sort_float(item.get("robust_oos_net_pnl")),
            -_sort_float(item.get("robust_net_pnl")),
            _sort_float(item.get("robust_maximum_drawdown")),
            int(item.get("pessimistic_harmed_winner_count", 0)),
            str(item.get("candidate_id")),
        ),
    )


def _evaluate_arm(
    frame: pd.DataFrame,
    mask: pd.Series,
    *,
    arm_id: str,
    global_metrics: Mapping[str, Any],
    global_oos: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    aligned = mask.reindex(frame.index).fillna(False).astype(bool)
    selected = frame.loc[aligned].copy()
    train = selected.loc[~selected["__ab_oos"]].copy()
    oos = selected.loc[selected["__ab_oos"]].copy()
    metrics = profit_metrics(selected)
    train_metrics = profit_metrics(train)
    oos_metrics = profit_metrics(oos)
    winner_to_loser = _winner_to_loser_mask(selected)

    no_protection = _no_protection_candidate(
        arm_id=arm_id,
        metrics=metrics,
        train_metrics=train_metrics,
        oos_metrics=oos_metrics,
        global_metrics=global_metrics,
        global_oos=global_oos,
        selected_count=len(selected),
        winner_to_loser_count=int(winner_to_loser.sum()),
    )
    protection = _build_protection_candidates(
        selected,
        arm_id=arm_id,
        arm_metrics=metrics,
        arm_oos_metrics=oos_metrics,
        global_metrics=global_metrics,
        global_oos=global_oos,
    )
    arm_result = {
        "arm_id": arm_id,
        "condition": _arm_condition(arm_id),
        "selected_trade_count": int(len(selected)),
        "train_trade_count": int(len(train)),
        "oos_trade_count": int(len(oos)),
        "winner_to_loser_count": int(winner_to_loser.sum()),
        "metrics": metrics,
        "train_metrics": train_metrics,
        "oos_metrics": oos_metrics,
        "best_protection_candidate": (
            rank_ab_candidates(protection)[0] if protection else None
        ),
    }
    return arm_result, [no_protection, *protection]


def _build_protection_candidates(
    frame: pd.DataFrame,
    *,
    arm_id: str,
    arm_metrics: Mapping[str, Any],
    arm_oos_metrics: Mapping[str, Any],
    global_metrics: Mapping[str, Any],
    global_oos: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    rows: list[dict[str, Any]] = []
    for trigger_pct in PROTECTION_TRIGGER_PCTS:
        for retention in PROTECTION_RETENTION_FRACTIONS:
            simulation = _simulate_protection(
                frame,
                trigger_pct=trigger_pct,
                retention_fraction=retention,
            )
            robust = profit_metrics(simulation["pessimistic_frame"])
            optimistic = profit_metrics(simulation["optimistic_frame"])
            robust_train = profit_metrics(
                simulation["pessimistic_frame"].loc[
                    ~simulation["pessimistic_frame"]["__ab_oos"]
                ]
            )
            robust_oos = profit_metrics(
                simulation["pessimistic_frame"].loc[
                    simulation["pessimistic_frame"]["__ab_oos"]
                ]
            )
            arm_delta = float(robust["net_pnl"]) - float(arm_metrics["net_pnl"])
            arm_oos_delta = float(robust_oos["net_pnl"]) - float(
                arm_oos_metrics["net_pnl"]
            )
            global_delta = float(robust["net_pnl"]) - float(
                global_metrics["net_pnl"]
            )
            global_oos_delta = float(robust_oos["net_pnl"]) - float(
                global_oos["net_pnl"]
            )
            decision = (
                "PROMOVER_PARA_PAPER_AB"
                if _robust_candidate_gate(
                    robust,
                    robust_oos,
                    arm_delta=arm_delta,
                    arm_oos_delta=arm_oos_delta,
                    selected_count=len(frame),
                    oos_count=int(frame["__ab_oos"].sum()),
                )
                else "MANTER_EM_RESEARCH"
            )
            protection_id = _protection_id(trigger_pct, retention)
            rows.append(
                {
                    "candidate_id": f"{arm_id}__{protection_id}",
                    "candidate_type": "momentum_plus_profit_protection",
                    "arm_id": arm_id,
                    "condition": _arm_condition(arm_id),
                    "protection_id": protection_id,
                    "trigger_mfe_pct": trigger_pct,
                    "retention_fraction_of_mfe": retention,
                    "selected_trade_count": int(len(frame)),
                    "oos_trade_count": int(frame["__ab_oos"].sum()),
                    "robust_net_pnl": robust["net_pnl"],
                    "robust_expectancy": robust["expectancy"],
                    "robust_profit_factor": robust["profit_factor"],
                    "robust_average_win": robust["average_win"],
                    "robust_average_loss": robust["average_loss"],
                    "robust_maximum_drawdown": robust["maximum_drawdown"],
                    "robust_train_net_pnl": robust_train["net_pnl"],
                    "robust_oos_net_pnl": robust_oos["net_pnl"],
                    "robust_oos_expectancy": robust_oos["expectancy"],
                    "robust_oos_profit_factor": robust_oos["profit_factor"],
                    "robust_delta_vs_arm": arm_delta,
                    "robust_oos_delta_vs_arm": arm_oos_delta,
                    "robust_delta_vs_global_baseline": global_delta,
                    "robust_oos_delta_vs_global_baseline": global_oos_delta,
                    "optimistic_net_pnl": optimistic["net_pnl"],
                    "optimistic_expectancy": optimistic["expectancy"],
                    "optimistic_profit_factor": optimistic["profit_factor"],
                    "optimistic_maximum_drawdown": optimistic[
                        "maximum_drawdown"
                    ],
                    **simulation["diagnostics"],
                    "decision": decision,
                    "operational_change_performed": False,
                }
            )
    return rows


def _simulate_protection(
    frame: pd.DataFrame,
    *,
    trigger_pct: float,
    retention_fraction: float,
) -> dict[str, Any]:
    work = frame.copy()
    net = pd.to_numeric(work["net_pnl"], errors="coerce")
    mfe_abs = pd.to_numeric(work.get("mfe_absolute"), errors="coerce")
    mfe_pct = pd.to_numeric(work.get("mfe_pct"), errors="coerce")
    retracement = pd.to_numeric(
        work.get("retracement_after_mfe_absolute"), errors="coerce"
    )
    fees = pd.to_numeric(work.get("fees"), errors="coerce")
    funding = pd.to_numeric(work.get("funding"), errors="coerce")
    costs = fees + funding
    floor_gross = pd.concat(
        [costs, mfe_abs * retention_fraction], axis=1
    ).max(axis=1, skipna=False)
    floor_net = floor_gross - costs
    complete = (
        net.notna()
        & mfe_abs.notna()
        & mfe_pct.notna()
        & retracement.notna()
        & fees.notna()
        & funding.notna()
    )
    armed = (
        complete
        & mfe_abs.gt(0.0)
        & mfe_pct.ge(trigger_pct)
        & floor_gross.le(mfe_abs)
    )
    distance_from_mfe = mfe_abs - floor_gross
    pessimistic_hit = armed & retracement.ge(distance_from_mfe - 1e-12)
    optimistic_hit = armed & net.lt(floor_net)

    optimistic_net = net.mask(optimistic_hit, floor_net)
    pessimistic_net = net.mask(pessimistic_hit, floor_net)
    optimistic_frame = work.copy()
    pessimistic_frame = work.copy()
    optimistic_frame["net_pnl"] = optimistic_net
    pessimistic_frame["net_pnl"] = pessimistic_net

    losers = net.lt(0.0)
    winners = net.gt(0.0)
    winner_to_loser = _winner_to_loser_mask(work)
    saved_loser = pessimistic_hit & losers & pessimistic_net.gt(net)
    harmed_winner = pessimistic_hit & winners & pessimistic_net.lt(net)
    recovered_wtl = pessimistic_hit & winner_to_loser & pessimistic_net.gt(net)

    return {
        "optimistic_frame": optimistic_frame,
        "pessimistic_frame": pessimistic_frame,
        "diagnostics": {
            "path_complete_trade_count": int(complete.sum()),
            "protection_armed_trade_count": int(armed.sum()),
            "optimistic_floor_hit_count": int(optimistic_hit.sum()),
            "pessimistic_floor_hit_count": int(pessimistic_hit.sum()),
            "pessimistic_saved_loser_count": int(saved_loser.sum()),
            "pessimistic_harmed_winner_count": int(harmed_winner.sum()),
            "pessimistic_recovered_winner_to_loser_count": int(
                recovered_wtl.sum()
            ),
            "pessimistic_recovered_winner_to_loser_pnl": float(
                (pessimistic_net.loc[recovered_wtl] - net.loc[recovered_wtl]).sum()
            ),
            "pessimistic_winner_pnl_sacrificed": float(
                (net.loc[harmed_winner] - pessimistic_net.loc[harmed_winner]).sum()
            ),
            "simulation_incomplete_trade_count": int((~complete).sum()),
        },
    }


def _no_protection_candidate(
    *,
    arm_id: str,
    metrics: Mapping[str, Any],
    train_metrics: Mapping[str, Any],
    oos_metrics: Mapping[str, Any],
    global_metrics: Mapping[str, Any],
    global_oos: Mapping[str, Any],
    selected_count: int,
    winner_to_loser_count: int,
) -> dict[str, Any]:
    delta = float(metrics["net_pnl"]) - float(global_metrics["net_pnl"])
    oos_delta = float(oos_metrics["net_pnl"]) - float(global_oos["net_pnl"])
    decision = (
        "PROMOVER_PARA_PAPER_AB"
        if arm_id != ARM_CONTROL
        and _positive_metrics(metrics)
        and _positive_metrics(oos_metrics)
        and delta > 0
        and oos_delta > 0
        and selected_count >= 8
        and int(oos_metrics["trade_count"]) >= 5
        else "MANTER_EM_RESEARCH"
    )
    return {
        "candidate_id": f"{arm_id}__none",
        "candidate_type": "momentum_only",
        "arm_id": arm_id,
        "condition": _arm_condition(arm_id),
        "protection_id": "none",
        "selected_trade_count": selected_count,
        "oos_trade_count": int(oos_metrics["trade_count"]),
        "robust_net_pnl": metrics["net_pnl"],
        "robust_expectancy": metrics["expectancy"],
        "robust_profit_factor": metrics["profit_factor"],
        "robust_average_win": metrics["average_win"],
        "robust_average_loss": metrics["average_loss"],
        "robust_maximum_drawdown": metrics["maximum_drawdown"],
        "robust_train_net_pnl": train_metrics["net_pnl"],
        "robust_oos_net_pnl": oos_metrics["net_pnl"],
        "robust_oos_expectancy": oos_metrics["expectancy"],
        "robust_oos_profit_factor": oos_metrics["profit_factor"],
        "robust_delta_vs_arm": 0.0,
        "robust_oos_delta_vs_arm": 0.0,
        "robust_delta_vs_global_baseline": delta,
        "robust_oos_delta_vs_global_baseline": oos_delta,
        "optimistic_net_pnl": metrics["net_pnl"],
        "optimistic_expectancy": metrics["expectancy"],
        "optimistic_profit_factor": metrics["profit_factor"],
        "optimistic_maximum_drawdown": metrics["maximum_drawdown"],
        "path_complete_trade_count": 0,
        "protection_armed_trade_count": 0,
        "optimistic_floor_hit_count": 0,
        "pessimistic_floor_hit_count": 0,
        "pessimistic_saved_loser_count": 0,
        "pessimistic_harmed_winner_count": 0,
        "pessimistic_recovered_winner_to_loser_count": 0,
        "pessimistic_recovered_winner_to_loser_pnl": 0.0,
        "pessimistic_winner_pnl_sacrificed": 0.0,
        "simulation_incomplete_trade_count": 0,
        "winner_to_loser_count": winner_to_loser_count,
        "decision": decision,
        "operational_change_performed": False,
    }


def _robust_candidate_gate(
    full: Mapping[str, Any],
    oos: Mapping[str, Any],
    *,
    arm_delta: float,
    arm_oos_delta: float,
    selected_count: int,
    oos_count: int,
) -> bool:
    return bool(
        selected_count >= 8
        and oos_count >= 5
        and _positive_metrics(full)
        and _positive_metrics(oos)
        and arm_delta > 0
        and arm_oos_delta > 0
    )


def _positive_metrics(metrics: Mapping[str, Any]) -> bool:
    profit_factor = finite_or_none(metrics.get("profit_factor"))
    return bool(
        float(metrics.get("net_pnl", 0.0)) > 0
        and float(metrics.get("expectancy", 0.0)) > 0
        and (profit_factor is None or profit_factor > 1.0)
    )


def _winner_to_loser_mask(frame: pd.DataFrame) -> pd.Series:
    if "winner_to_loser_conversion" in frame.columns:
        values = frame["winner_to_loser_conversion"]
        return values.eq(True).fillna(False).astype(bool)  # noqa: E712
    net = pd.to_numeric(frame.get("net_pnl"), errors="coerce")
    mfe = pd.to_numeric(frame.get("mfe_absolute"), errors="coerce")
    return net.lt(0.0) & mfe.gt(0.0)


def _arm_condition(arm_id: str) -> dict[str, Any]:
    if arm_id == ARM_RET12:
        return {
            "field": "entry_return_12",
            "operator": "gte",
            "value": RET12_THRESHOLD,
        }
    if arm_id == ARM_RET12_RET1:
        return {
            "operator": "and",
            "conditions": [
                {
                    "field": "entry_return_12",
                    "operator": "gte",
                    "value": RET12_THRESHOLD,
                },
                {
                    "field": "entry_return_1",
                    "operator": "gte",
                    "value": RET1_THRESHOLD,
                },
            ],
        }
    return {"operator": "all_eligible"}


def _protection_id(trigger_pct: float, retention: float) -> str:
    trigger_bps = int(round(trigger_pct * 10000))
    retention_pct = int(round(retention * 100))
    label = "net_breakeven" if retention_pct == 0 else f"retain_{retention_pct}pct_mfe"
    return f"trigger_{trigger_bps}bps__{label}"


def _sort_float(value: Any) -> float:
    parsed = finite_or_none(value)
    return parsed if parsed is not None and math.isfinite(parsed) else -math.inf
