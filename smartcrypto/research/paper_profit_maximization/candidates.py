"""Candidate generation and temporal profit ranking."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from .contracts import CATEGORICAL_ENTRY_FEATURES, NUMERIC_ENTRY_FEATURES, SCORE_FIELDS
from .metrics import finite_or_none, first_finite, profit_metrics, sort_trades


def build_entry_filter_candidates(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    candidates: list[dict[str, Any]] = []
    for field in NUMERIC_ENTRY_FEATURES:
        if field not in frame.columns:
            continue
        numeric = pd.to_numeric(frame[field], errors="coerce")
        if numeric.notna().sum() < minimum_candidate_trades(len(frame)):
            continue
        for quantile in (0.25, 0.50, 0.75):
            threshold = float(numeric.quantile(quantile))
            if not math.isfinite(threshold):
                continue
            for operator in ("gte", "lte"):
                mask = numeric.ge(threshold) if operator == "gte" else numeric.le(threshold)
                candidate = evaluate_keep_candidate(
                    frame,
                    mask,
                    candidate_id=f"entry_{field}_{operator}_q{int(quantile * 100)}",
                    candidate_type="entry_feature_filter",
                    condition={"field": field, "operator": operator, "value": threshold},
                )
                if candidate is not None:
                    candidates.append(candidate)
    for field in CATEGORICAL_ENTRY_FEATURES:
        if field not in frame.columns:
            continue
        values = frame[field].astype("string").fillna("unknown")
        for value in sorted(values.unique().tolist()):
            if str(value).casefold() in {"unknown", "<na>", "nan", "none"}:
                continue
            candidate = evaluate_keep_candidate(
                frame,
                values.eq(value),
                candidate_id=f"entry_{field}_eq_{slug(value)}",
                candidate_type="entry_category_filter",
                condition={"field": field, "operator": "equals", "value": str(value)},
            )
            if candidate is not None:
                candidates.append(candidate)
    return candidates


def build_score_threshold_candidates(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    candidates: list[dict[str, Any]] = []
    for field in SCORE_FIELDS:
        if field not in frame.columns:
            continue
        numeric = pd.to_numeric(frame[field], errors="coerce")
        coverage = int(numeric.notna().sum())
        if coverage < minimum_candidate_trades(len(frame)):
            continue
        for quantile in (0.40, 0.50, 0.60, 0.70, 0.80):
            threshold = float(numeric.quantile(quantile))
            if not math.isfinite(threshold):
                continue
            candidate = evaluate_keep_candidate(
                frame,
                numeric.ge(threshold),
                candidate_id=f"score_{field}_gte_q{int(quantile * 100)}",
                candidate_type="ai_qlib_threshold",
                condition={"field": field, "operator": "gte", "value": threshold},
            )
            if candidate is not None:
                candidate["score_coverage_count"] = coverage
                candidates.append(candidate)
    return candidates


def build_combined_filter_candidates(
    frame: pd.DataFrame,
    candidates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for left_index, left in enumerate(candidates):
        left_condition = left.get("condition")
        if not isinstance(left_condition, Mapping):
            continue
        left_mask = condition_mask(frame, left_condition)
        if left_mask is None:
            continue
        for right in candidates[left_index + 1 :]:
            right_condition = right.get("condition")
            if not isinstance(right_condition, Mapping):
                continue
            if left_condition.get("field") == right_condition.get("field"):
                continue
            right_mask = condition_mask(frame, right_condition)
            if right_mask is None:
                continue
            candidate = evaluate_keep_candidate(
                frame,
                left_mask & right_mask,
                candidate_id=f"combo__{left.get('candidate_id')}__{right.get('candidate_id')}",
                candidate_type="combined_entry_ai_filter",
                condition={
                    "operator": "and",
                    "conditions": [dict(left_condition), dict(right_condition)],
                },
            )
            if candidate is not None:
                rows.append(candidate)
    return rank_candidates(rows)[:30]


def evaluate_keep_candidate(
    frame: pd.DataFrame,
    keep_mask: pd.Series,
    *,
    candidate_id: str,
    candidate_type: str,
    condition: Mapping[str, Any],
) -> dict[str, Any] | None:
    if len(keep_mask) != len(frame):
        return None
    work = frame.copy()
    aligned_mask = keep_mask.reindex(frame.index).fillna(False).astype(bool)
    work["__profit_candidate_keep"] = aligned_mask
    ordered = sort_trades(work).reset_index(drop=True)
    mask = ordered.pop("__profit_candidate_keep").astype(bool)
    selected_count = int(mask.sum())
    minimum = minimum_candidate_trades(len(ordered))
    if selected_count < minimum or selected_count >= len(ordered):
        return None
    baseline = profit_metrics(ordered)
    selected = ordered.loc[mask]
    candidate_metrics = profit_metrics(selected)
    rejected = ordered.loc[~mask]
    split = max(1, int(len(ordered) * 0.70))
    train = ordered.iloc[:split]
    oos = ordered.iloc[split:]
    train_mask = mask.iloc[:split]
    oos_mask = mask.iloc[split:]
    if int(train_mask.sum()) < 2 or int(oos_mask.sum()) < 2:
        return None
    train_baseline = profit_metrics(train)
    train_candidate = profit_metrics(train.loc[train_mask])
    oos_baseline = profit_metrics(oos)
    oos_candidate = profit_metrics(oos.loc[oos_mask])
    rejected_positive = pd.to_numeric(
        rejected.loc[pd.to_numeric(rejected["net_pnl"], errors="coerce") > 0, "net_pnl"],
        errors="coerce",
    ).sum()
    total_positive = float(baseline["gross_profit"])
    selected_positive = float(candidate_metrics["gross_profit"])
    delta_pnl = float(candidate_metrics["net_pnl"]) - float(baseline["net_pnl"])
    oos_delta = float(oos_candidate["net_pnl"]) - float(oos_baseline["net_pnl"])
    positive_candidate = positive_candidate_gate(
        candidate_metrics, oos_candidate, delta_pnl, oos_delta
    )
    return {
        "candidate_id": candidate_id,
        "candidate_type": candidate_type,
        "condition": dict(condition),
        "selected_trade_count": selected_count,
        "rejected_trade_count": int((~mask).sum()),
        "baseline_net_pnl": baseline["net_pnl"],
        "candidate_net_pnl": candidate_metrics["net_pnl"],
        "delta_pnl": delta_pnl,
        "baseline_expectancy": baseline["expectancy"],
        "candidate_expectancy": candidate_metrics["expectancy"],
        "baseline_profit_factor": baseline["profit_factor"],
        "candidate_profit_factor": candidate_metrics["profit_factor"],
        "baseline_average_win": baseline["average_win"],
        "candidate_average_win": candidate_metrics["average_win"],
        "baseline_average_loss": baseline["average_loss"],
        "candidate_average_loss": candidate_metrics["average_loss"],
        "baseline_maximum_drawdown": baseline["maximum_drawdown"],
        "candidate_maximum_drawdown": candidate_metrics["maximum_drawdown"],
        "train_delta_pnl": float(train_candidate["net_pnl"])
        - float(train_baseline["net_pnl"]),
        "out_of_sample_net_pnl": oos_candidate["net_pnl"],
        "out_of_sample_expectancy": oos_candidate["expectancy"],
        "out_of_sample_profit_factor": oos_candidate["profit_factor"],
        "out_of_sample_delta_pnl": oos_delta,
        "positive_pnl_retention_ratio": (
            selected_positive / total_positive if total_positive > 0 else None
        ),
        "positive_pnl_rejected": float(rejected_positive),
        "winner_capture_ratio_mean": candidate_metrics["winner_capture_ratio_mean"],
        "decision": "PROMOVER_PARA_PAPER_AB"
        if positive_candidate
        else "MANTER_EM_RESEARCH",
        "operational_change_performed": False,
    }


def standardize_exit_candidates(
    frame: pd.DataFrame,
    candidates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    baseline = profit_metrics(frame)
    split = max(1, int(len(frame) * 0.70))
    oos_baseline = profit_metrics(sort_trades(frame).iloc[split:])
    rows: list[dict[str, Any]] = []
    for item in candidates:
        candidate_net = first_finite(item.get("candidate_net_pnl"))
        delta = first_finite(item.get("delta_pnl"))
        oos_delta = first_finite(item.get("out_of_sample_delta_pnl"))
        if candidate_net is None or delta is None or oos_delta is None:
            continue
        candidate_pf = first_finite(item.get("candidate_profit_factor"))
        candidate_dd = first_finite(item.get("candidate_maximum_drawdown"))
        candidate_expectancy = candidate_net / max(int(baseline["trade_count"]), 1)
        oos_candidate_net = float(oos_baseline["net_pnl"]) + oos_delta
        oos_expectancy = oos_candidate_net / max(int(oos_baseline["trade_count"]), 1)
        positive = bool(
            candidate_net > 0
            and oos_candidate_net > 0
            and delta > 0
            and oos_delta > 0
            and candidate_expectancy > 0
            and oos_expectancy > 0
            and (candidate_pf is None or candidate_pf > 1.0)
        )
        rows.append(
            {
                "candidate_id": str(item.get("strategy_id") or item.get("candidate_id")),
                "candidate_type": "exit_policy",
                "condition": dict(item.get("configuration") or {}),
                "selected_trade_count": int(baseline["trade_count"]),
                "rejected_trade_count": 0,
                "baseline_net_pnl": baseline["net_pnl"],
                "candidate_net_pnl": candidate_net,
                "delta_pnl": delta,
                "baseline_expectancy": baseline["expectancy"],
                "candidate_expectancy": candidate_expectancy,
                "baseline_profit_factor": baseline["profit_factor"],
                "candidate_profit_factor": candidate_pf,
                "baseline_average_win": baseline["average_win"],
                "candidate_average_win": None,
                "baseline_average_loss": baseline["average_loss"],
                "candidate_average_loss": None,
                "baseline_maximum_drawdown": baseline["maximum_drawdown"],
                "candidate_maximum_drawdown": candidate_dd,
                "train_delta_pnl": None,
                "out_of_sample_net_pnl": oos_candidate_net,
                "out_of_sample_expectancy": oos_expectancy,
                "out_of_sample_profit_factor": None,
                "out_of_sample_delta_pnl": oos_delta,
                "positive_pnl_retention_ratio": 1.0,
                "positive_pnl_rejected": 0.0,
                "winner_capture_ratio_mean": None,
                "decision": "PROMOVER_PARA_PAPER_AB"
                if positive
                else "MANTER_EM_RESEARCH",
                "operational_change_performed": False,
            }
        )
    return rows


def rank_candidates(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [dict(item) for item in candidates],
        key=lambda item: (
            item.get("decision") != "PROMOVER_PARA_PAPER_AB",
            -sort_float(item.get("out_of_sample_delta_pnl")),
            -sort_float(item.get("delta_pnl")),
            -sort_float(item.get("candidate_expectancy")),
            -sort_float(item.get("positive_pnl_retention_ratio")),
            str(item.get("candidate_id")),
        ),
    )


def condition_mask(frame: pd.DataFrame, condition: Mapping[str, Any]) -> pd.Series | None:
    field = str(condition.get("field") or "")
    operator = str(condition.get("operator") or "")
    if not field or field not in frame.columns:
        return None
    value = condition.get("value")
    if operator == "equals":
        return frame[field].astype("string").eq(str(value))
    numeric = pd.to_numeric(frame[field], errors="coerce")
    threshold = finite_or_none(value)
    if threshold is None:
        return None
    if operator == "gte":
        return numeric.ge(threshold)
    if operator == "lte":
        return numeric.le(threshold)
    return None


def positive_candidate_gate(
    full: Mapping[str, Any],
    oos: Mapping[str, Any],
    delta_pnl: float,
    oos_delta_pnl: float,
) -> bool:
    full_pf = first_finite(full.get("profit_factor"))
    oos_pf = first_finite(oos.get("profit_factor"))
    return bool(
        float(full.get("net_pnl", 0.0)) > 0
        and float(oos.get("net_pnl", 0.0)) > 0
        and float(full.get("expectancy", 0.0)) > 0
        and float(oos.get("expectancy", 0.0)) > 0
        and delta_pnl > 0
        and oos_delta_pnl > 0
        and (full_pf is None or full_pf > 1.0)
        and (oos_pf is None or oos_pf > 1.0)
    )


def minimum_candidate_trades(total: int) -> int:
    return min(total, max(8, int(math.ceil(total * 0.10))))


def sort_float(value: Any) -> float:
    parsed = finite_or_none(value)
    return parsed if parsed is not None else -math.inf


def slug(value: Any) -> str:
    text = str(value).strip().lower()
    normalized = "".join(
        character if character.isalnum() else "_" for character in text
    ).strip("_")
    return normalized or "unknown"
