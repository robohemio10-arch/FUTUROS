"""Profit-first entry, model-threshold and joint exit candidate simulation."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from smartcrypto.research.profit_research.paper_analysis import (
    _candle_groups,
    _simulate_exit_config,
    financial_metrics,
)

from .contracts import CATEGORICAL_DIMENSIONS, NUMERIC_DIMENSIONS
from .research import _winner_capture_summary
from .utils import (
    _finite,
    _first_finite,
    _minimum_trades,
    _pf_improved,
    _slug,
    _sort_float,
    _sum_pnl,
)


def _build_filter_candidates(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    candidates: list[dict[str, Any]] = []
    for dimension in CATEGORICAL_DIMENSIONS:
        if dimension not in frame.columns:
            continue
        values = frame[dimension].astype("string").fillna("unknown")
        for value in sorted(values.unique().tolist()):
            if str(value).casefold() in {"unknown", "<na>", "nan", "none"}:
                continue
            remove_mask = values.eq(value)
            if _sum_pnl(frame.loc[remove_mask]) >= 0:
                continue
            candidate = _evaluate_keep_candidate(
                frame,
                ~remove_mask,
                candidate_id=f"exclude_{dimension}_{_slug(value)}",
                condition={
                    "field": dimension,
                    "operator": "not_equals",
                    "value": str(value),
                },
            )
            if candidate:
                candidates.append(candidate)
    for dimension in NUMERIC_DIMENSIONS:
        if dimension not in frame.columns:
            continue
        numeric = pd.to_numeric(frame[dimension], errors="coerce")
        if numeric.notna().sum() < _minimum_trades(len(frame)):
            continue
        for quantile in (0.25, 0.50, 0.75):
            threshold = float(numeric.quantile(quantile))
            if not math.isfinite(threshold):
                continue
            for operator in ("gte", "lte"):
                keep = numeric.ge(threshold) if operator == "gte" else numeric.le(threshold)
                candidate = _evaluate_keep_candidate(
                    frame,
                    keep,
                    candidate_id=(
                        f"keep_{dimension}_{operator}_q{int(quantile * 100)}"
                    ),
                    condition={
                        "field": dimension,
                        "operator": operator,
                        "value": threshold,
                    },
                )
                if candidate:
                    candidates.append(candidate)
    return _rank_candidates(candidates)


def _build_combined_candidates(
    frame: pd.DataFrame,
    singles: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for left_index, left in enumerate(singles):
        left_condition = left.get("condition")
        if not isinstance(left_condition, Mapping):
            continue
        left_mask = _condition_mask(frame, left_condition)
        if left_mask is None:
            continue
        for right in singles[left_index + 1 :]:
            right_condition = right.get("condition")
            if not isinstance(right_condition, Mapping):
                continue
            if left_condition.get("field") == right_condition.get("field"):
                continue
            right_mask = _condition_mask(frame, right_condition)
            if right_mask is None:
                continue
            candidate = _evaluate_keep_candidate(
                frame,
                left_mask & right_mask,
                candidate_id=(
                    f"combo__{left['candidate_id']}__{right['candidate_id']}"
                ),
                condition={
                    "operator": "and",
                    "conditions": [dict(left_condition), dict(right_condition)],
                },
            )
            if candidate:
                candidate["candidate_type"] = "combined_entry_ai_filter"
                rows.append(candidate)
    return _rank_candidates(rows)


def _build_joint_candidates(
    frame: pd.DataFrame,
    filters: Sequence[Mapping[str, Any]],
    exit_candidates: Sequence[Mapping[str, Any]],
    candles: pd.DataFrame,
) -> list[dict[str, Any]]:
    """Simulate entry/AI filters and exit policies on the same trade universe."""

    rows: list[dict[str, Any]] = []
    if frame.empty:
        return rows
    baseline = financial_metrics(frame)
    split = max(1, int(len(frame) * 0.70))
    oos_baseline_frame = frame.iloc[split:].copy()
    oos_baseline = financial_metrics(oos_baseline_frame)
    candle_groups = _candle_groups(candles) if not candles.empty else {}

    for filter_candidate in filters:
        condition = filter_candidate.get("condition")
        if not isinstance(condition, Mapping):
            continue
        keep_mask = _condition_mask(frame, condition)
        if keep_mask is None:
            continue
        filtered = frame.loc[keep_mask].copy()
        if filtered.empty:
            continue
        oos_keep = keep_mask.iloc[split:]
        filtered_oos = oos_baseline_frame.loc[oos_keep].copy()
        for exit_candidate in exit_candidates:
            config = exit_candidate.get("configuration")
            if not isinstance(config, Mapping):
                continue
            if not candle_groups:
                exit_delta = _first_finite(exit_candidate.get("delta_pnl"))
                exit_oos_delta = _first_finite(
                    exit_candidate.get("out_of_sample_delta_pnl")
                )
                if (
                    exit_delta is not None
                    and exit_oos_delta is not None
                    and exit_delta > 0
                    and exit_oos_delta > 0
                ):
                    rows.append(
                        {
                            "candidate_id": (
                                "joint__"
                                f"{filter_candidate['candidate_id']}__"
                                f"{exit_candidate.get('strategy_id')}"
                            ),
                            "candidate_type": "entry_ai_plus_exit_replay_required",
                            "condition": dict(condition),
                            "exit_policy": dict(config),
                            "baseline_net_pnl": baseline.get("net_pnl"),
                            "filter_candidate_net_pnl": filter_candidate.get(
                                "candidate_net_pnl"
                            ),
                            "filter_delta_pnl": filter_candidate.get("delta_pnl"),
                            "exit_delta_pnl_independent": exit_delta,
                            "exit_out_of_sample_delta_pnl_independent": exit_oos_delta,
                            "winner_capture_ratio": filter_candidate.get(
                                "winner_capture_ratio"
                            ),
                            "decision": (
                                "REPLAY_COMBINADO_OBRIGATORIO_ANTES_DE_PAPER"
                            ),
                            "combined_net_pnl_claimed": False,
                            "operational_change_performed": False,
                        }
                    )
                continue

            simulated = _simulate_exit_config(filtered, candle_groups, config)
            candidate_frame = filtered.copy()
            valid = simulated["candidate_net_pnl"].notna()
            candidate_frame["candidate_net_pnl"] = np.where(
                valid,
                simulated["candidate_net_pnl"],
                candidate_frame["net_pnl"],
            )
            if "slippage_cost" in candidate_frame.columns:
                slippage = pd.to_numeric(
                    candidate_frame["slippage_cost"], errors="coerce"
                ).fillna(0.0)
                candidate_frame["candidate_net_pnl"] = (
                    pd.to_numeric(
                        candidate_frame["candidate_net_pnl"], errors="coerce"
                    )
                    - slippage
                )
            metrics = financial_metrics(
                candidate_frame, pnl_column="candidate_net_pnl"
            )

            if filtered_oos.empty:
                continue
            simulated_oos = _simulate_exit_config(
                filtered_oos, candle_groups, config
            )
            candidate_oos = filtered_oos.copy()
            valid_oos = simulated_oos["candidate_net_pnl"].notna()
            candidate_oos["candidate_net_pnl"] = np.where(
                valid_oos,
                simulated_oos["candidate_net_pnl"],
                candidate_oos["net_pnl"],
            )
            if "slippage_cost" in candidate_oos.columns:
                oos_slippage = pd.to_numeric(
                    candidate_oos["slippage_cost"], errors="coerce"
                ).fillna(0.0)
                candidate_oos["candidate_net_pnl"] = (
                    pd.to_numeric(
                        candidate_oos["candidate_net_pnl"], errors="coerce"
                    )
                    - oos_slippage
                )
            oos_metrics = financial_metrics(
                candidate_oos, pnl_column="candidate_net_pnl"
            )
            delta = float(metrics["net_pnl"]) - float(baseline["net_pnl"])
            oos_delta = float(oos_metrics["net_pnl"]) - float(
                oos_baseline["net_pnl"]
            )
            capture = _winner_capture_from_candidate_pnl(candidate_frame)
            baseline_capture = _winner_capture_summary(frame).get(
                "winner_capture_ratio"
            )
            candidate_pf = _finite(metrics.get("profit_factor"))
            baseline_pf = _finite(baseline.get("profit_factor"))
            oos_pf = _finite(oos_metrics.get("profit_factor"))
            avg_loss = float(metrics.get("average_loss", 0.0))
            baseline_avg_loss = float(baseline.get("average_loss", 0.0))
            dd = float(metrics.get("maximum_drawdown", 0.0))
            baseline_dd = float(baseline.get("maximum_drawdown", 0.0))
            core = bool(
                float(metrics["net_pnl"]) > float(baseline["net_pnl"])
                and float(metrics["expectancy"]) > float(baseline["expectancy"])
                and delta > 0
                and oos_delta > 0
                and (candidate_pf is None or candidate_pf > 1.0)
                and (oos_pf is None or oos_pf > 1.0)
            )
            supporting = sum(
                (
                    float(metrics.get("average_win", 0.0))
                    >= float(baseline.get("average_win", 0.0)),
                    abs(avg_loss) <= abs(baseline_avg_loss),
                    dd <= baseline_dd * 1.10 + 1e-12,
                    capture is None
                    or baseline_capture is None
                    or capture >= baseline_capture,
                )
            )
            rows.append(
                {
                    "candidate_id": (
                        "joint__"
                        f"{filter_candidate['candidate_id']}__"
                        f"{exit_candidate.get('strategy_id')}"
                    ),
                    "candidate_type": "entry_ai_plus_exit_simulation",
                    "condition": dict(condition),
                    "exit_policy": dict(config),
                    "selected_trade_count": int(len(candidate_frame)),
                    "trades_with_counterfactual_exit": int(valid.sum()),
                    "baseline_net_pnl": baseline["net_pnl"],
                    "candidate_net_pnl": metrics["net_pnl"],
                    "delta_pnl": delta,
                    "baseline_expectancy": baseline["expectancy"],
                    "candidate_expectancy": metrics["expectancy"],
                    "baseline_profit_factor": baseline_pf,
                    "candidate_profit_factor": candidate_pf,
                    "baseline_win_rate": baseline["win_rate"],
                    "candidate_win_rate": metrics["win_rate"],
                    "baseline_average_win": baseline["average_win"],
                    "candidate_average_win": metrics["average_win"],
                    "baseline_average_loss": baseline_avg_loss,
                    "candidate_average_loss": avg_loss,
                    "baseline_maximum_drawdown": baseline_dd,
                    "candidate_maximum_drawdown": dd,
                    "baseline_winner_capture_ratio": baseline_capture,
                    "winner_capture_ratio": capture,
                    "winner_capture_ratio_basis": (
                        "candidate_realized_pnl_over_original_trade_mfe"
                    ),
                    "out_of_sample_net_pnl": oos_metrics["net_pnl"],
                    "out_of_sample_expectancy": oos_metrics["expectancy"],
                    "out_of_sample_profit_factor": oos_pf,
                    "out_of_sample_delta_pnl": oos_delta,
                    "financial_objective_improvements": {
                        "net_pnl_up": float(metrics["net_pnl"])
                        > float(baseline["net_pnl"]),
                        "expectancy_up": float(metrics["expectancy"])
                        > float(baseline["expectancy"]),
                        "profit_factor_up": _pf_improved(
                            candidate_pf, baseline_pf
                        ),
                        "avg_win_up": float(metrics["average_win"])
                        >= float(baseline["average_win"]),
                        "winner_capture_ratio_up": (
                            capture is None
                            or baseline_capture is None
                            or capture >= baseline_capture
                        ),
                        "avg_loss_magnitude_down": abs(avg_loss)
                        <= abs(baseline_avg_loss),
                        "max_drawdown_controlled": dd
                        <= baseline_dd * 1.10 + 1e-12,
                        "oos_net_pnl_up": oos_delta > 0,
                    },
                    "decision": (
                        "PROMOVER_PARA_PAPER_BACKTEST"
                        if core and supporting >= 2
                        else "MANTER_EM_RESEARCH"
                    ),
                    "combined_net_pnl_claimed": True,
                    "same_candle_rule": "stop_loss_first",
                    "operational_change_performed": False,
                }
            )
    simulated = [row for row in rows if row.get("combined_net_pnl_claimed") is True]
    replay_required = [
        row for row in rows if row.get("combined_net_pnl_claimed") is not True
    ]
    return [*_rank_candidates(simulated), *replay_required]


def _winner_capture_from_candidate_pnl(frame: pd.DataFrame) -> float | None:
    if frame.empty or "candidate_net_pnl" not in frame.columns:
        return None
    candidate_pnl = pd.to_numeric(frame["candidate_net_pnl"], errors="coerce")
    mfe = pd.to_numeric(frame.get("max_unrealized_profit"), errors="coerce")
    valid = candidate_pnl.gt(0) & mfe.gt(0)
    if not bool(valid.any()):
        return None
    ratios = candidate_pnl.loc[valid] / mfe.loc[valid]
    ratios = ratios.replace([np.inf, -np.inf], np.nan).dropna()
    return float(ratios.mean()) if not ratios.empty else None


def _evaluate_keep_candidate(
    frame: pd.DataFrame,
    keep_mask: pd.Series,
    *,
    candidate_id: str,
    condition: Mapping[str, Any],
) -> dict[str, Any] | None:
    if len(frame) < 4 or len(keep_mask) != len(frame):
        return None
    ordered = frame.copy()
    ordered["__keep"] = keep_mask.reindex(frame.index).fillna(False).astype(bool)
    sort_columns = [
        column for column in ("close_time_utc", "trade_id") if column in ordered
    ]
    if sort_columns:
        ordered = ordered.sort_values(sort_columns, na_position="last")
    ordered = ordered.reset_index(drop=True)
    keep = ordered.pop("__keep").astype(bool)
    minimum = _minimum_trades(len(ordered))
    if int(keep.sum()) < minimum or int(keep.sum()) >= len(ordered):
        return None
    split = max(1, int(len(ordered) * 0.70))
    if split >= len(ordered):
        return None
    train_mask = keep.iloc[:split]
    oos_mask = keep.iloc[split:]
    if int(train_mask.sum()) < 2 or int(oos_mask.sum()) < 2:
        return None
    baseline = financial_metrics(ordered)
    candidate_frame = ordered.loc[keep]
    candidate = financial_metrics(candidate_frame)
    train_baseline = financial_metrics(ordered.iloc[:split])
    train_candidate = financial_metrics(ordered.iloc[:split].loc[train_mask])
    oos_baseline = financial_metrics(ordered.iloc[split:])
    oos_candidate = financial_metrics(ordered.iloc[split:].loc[oos_mask])
    delta = float(candidate["net_pnl"]) - float(baseline["net_pnl"])
    oos_delta = float(oos_candidate["net_pnl"]) - float(oos_baseline["net_pnl"])
    capture = _winner_capture_summary(candidate_frame).get("winner_capture_ratio")
    baseline_capture = _winner_capture_summary(ordered).get("winner_capture_ratio")
    pf = _finite(candidate.get("profit_factor"))
    baseline_pf = _finite(baseline.get("profit_factor"))
    oos_pf = _finite(oos_candidate.get("profit_factor"))
    avg_loss = float(candidate.get("average_loss", 0.0))
    baseline_avg_loss = float(baseline.get("average_loss", 0.0))
    dd = float(candidate.get("maximum_drawdown", 0.0))
    baseline_dd = float(baseline.get("maximum_drawdown", 0.0))
    core = bool(
        float(candidate["net_pnl"]) > float(baseline["net_pnl"])
        and float(candidate["expectancy"]) > float(baseline["expectancy"])
        and delta > 0
        and oos_delta > 0
        and (pf is None or pf > 1.0)
        and (oos_pf is None or oos_pf > 1.0)
    )
    supporting = sum(
        (
            float(candidate.get("average_win", 0.0))
            >= float(baseline.get("average_win", 0.0)),
            abs(avg_loss) <= abs(baseline_avg_loss),
            dd <= baseline_dd * 1.10 + 1e-12,
            capture is None
            or baseline_capture is None
            or capture >= baseline_capture,
        )
    )
    return {
        "candidate_id": candidate_id,
        "candidate_type": "entry_or_model_filter",
        "condition": dict(condition),
        "selected_trade_count": int(keep.sum()),
        "rejected_trade_count": int((~keep).sum()),
        "baseline_net_pnl": baseline["net_pnl"],
        "candidate_net_pnl": candidate["net_pnl"],
        "delta_pnl": delta,
        "baseline_expectancy": baseline["expectancy"],
        "candidate_expectancy": candidate["expectancy"],
        "baseline_profit_factor": baseline_pf,
        "candidate_profit_factor": pf,
        "baseline_win_rate": baseline["win_rate"],
        "candidate_win_rate": candidate["win_rate"],
        "baseline_average_win": baseline["average_win"],
        "candidate_average_win": candidate["average_win"],
        "baseline_average_loss": baseline_avg_loss,
        "candidate_average_loss": avg_loss,
        "baseline_maximum_drawdown": baseline_dd,
        "candidate_maximum_drawdown": dd,
        "baseline_winner_capture_ratio": baseline_capture,
        "winner_capture_ratio": capture,
        "train_delta_pnl": (
            float(train_candidate["net_pnl"]) - float(train_baseline["net_pnl"])
        ),
        "out_of_sample_net_pnl": oos_candidate["net_pnl"],
        "out_of_sample_expectancy": oos_candidate["expectancy"],
        "out_of_sample_profit_factor": oos_pf,
        "out_of_sample_delta_pnl": oos_delta,
        "financial_objective_improvements": {
            "net_pnl_up": float(candidate["net_pnl"]) > float(baseline["net_pnl"]),
            "expectancy_up": (
                float(candidate["expectancy"]) > float(baseline["expectancy"])
            ),
            "profit_factor_up": _pf_improved(pf, baseline_pf),
            "avg_win_up": (
                float(candidate["average_win"]) >= float(baseline["average_win"])
            ),
            "winner_capture_ratio_up": (
                capture is None
                or baseline_capture is None
                or capture >= baseline_capture
            ),
            "avg_loss_magnitude_down": abs(avg_loss) <= abs(baseline_avg_loss),
            "max_drawdown_controlled": dd <= baseline_dd * 1.10 + 1e-12,
            "oos_net_pnl_up": oos_delta > 0,
        },
        "decision": (
            "PROMOVER_PARA_PAPER_BACKTEST"
            if core and supporting >= 2
            else "MANTER_EM_RESEARCH"
        ),
        "operational_change_performed": False,
    }


def _condition_mask(
    frame: pd.DataFrame,
    condition: Mapping[str, Any],
) -> pd.Series | None:
    operator = str(condition.get("operator") or "")
    if operator == "and":
        conditions = condition.get("conditions")
        if not isinstance(conditions, list):
            return None
        masks = [
            _condition_mask(frame, item)
            for item in conditions
            if isinstance(item, Mapping)
        ]
        if not masks or any(mask is None for mask in masks):
            return None
        result = pd.Series(True, index=frame.index, dtype=bool)
        for mask in masks:
            if mask is None:
                return None
            result &= mask
        return result
    field = str(condition.get("field") or "")
    if not field or field not in frame.columns:
        return None
    value = condition.get("value")
    if operator == "not_equals":
        return ~frame[field].astype("string").eq(str(value))
    numeric = pd.to_numeric(frame[field], errors="coerce")
    threshold = _finite(value)
    if threshold is None:
        return None
    if operator == "gte":
        return numeric.ge(threshold)
    if operator == "lte":
        return numeric.le(threshold)
    return None


def _rank_candidates(
    candidates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    eligible = [
        dict(item) for item in candidates if item.get("candidate_net_pnl") is not None
    ]
    return sorted(
        eligible,
        key=lambda item: (
            item.get("decision") != "PROMOVER_PARA_PAPER_BACKTEST",
            -_sort_float(item.get("out_of_sample_delta_pnl")),
            -_sort_float(item.get("delta_pnl")),
            -_sort_float(item.get("candidate_expectancy")),
            str(item.get("candidate_id")),
        ),
    )
