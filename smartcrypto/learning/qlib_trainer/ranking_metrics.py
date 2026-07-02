"""Ranking metrics for research-only challenger evaluation."""

from __future__ import annotations

from typing import Any

import pandas as pd


def compute_ranking_metrics(
    *,
    split_id: str,
    train_row_count: int,
    validation_row_count: int,
    test_frame: pd.DataFrame,
    predictions: pd.Series,
    baseline_summary: dict[str, Any],
) -> dict[str, Any]:
    target = pd.to_numeric(test_frame["target_expected_value_component"], errors="coerce").fillna(0.0)
    label = pd.to_numeric(test_frame.get("target_label_sign", 0), errors="coerce").fillna(0).astype(int)
    profit_ratio = pd.to_numeric(test_frame.get("target_profit_ratio", 0.0), errors="coerce").fillna(0.0)
    pred = pd.to_numeric(predictions, errors="coerce").fillna(0.0)
    precision5 = precision_at_k(label, pred, 5)
    precision10 = precision_at_k(label, pred, 10)
    recall10 = recall_at_k(label, pred, 10)
    top_decile, bottom_decile = decile_values(target, pred)
    top_k = selected_top_k(test_frame, pred, 10)
    selected_ev = rounded_sum(top_k["target_expected_value_component"])
    selected_win_rate = round(float((pd.to_numeric(top_k.get("target_label_sign", 0), errors="coerce") > 0).mean()), 10) if not top_k.empty else 0.0
    selected_avg_profit_ratio = rounded_mean(top_k.get("target_profit_ratio", pd.Series(dtype=float)))
    random_baseline = float(baseline_summary.get("random_deterministic_expected_value", 0.0) or 0.0)
    always_allow = float(baseline_summary.get("always_allow_expected_value", 0.0) or 0.0)
    no_trade = float(baseline_summary.get("no_trade_expected_value", 0.0) or 0.0)
    return {
        "split_id": split_id,
        "train_row_count": int(train_row_count),
        "validation_row_count": int(validation_row_count),
        "test_row_count": int(len(test_frame)),
        "rank_ic": corr(pred.rank(method="average"), target.rank(method="average")),
        "spearman_ic": corr(pred, target, method="spearman"),
        "pearson_ic": corr(pred, target, method="pearson"),
        "precision_at_5": precision5,
        "precision_at_10": precision10,
        "recall_at_10": recall10,
        "top_decile_expected_value": top_decile,
        "bottom_decile_expected_value": bottom_decile,
        "long_short_spread_expected_value": round(top_decile - bottom_decile, 10),
        "selected_top_k_expected_value": selected_ev,
        "selected_top_k_win_rate": selected_win_rate,
        "selected_top_k_avg_profit_ratio": selected_avg_profit_ratio,
        "baseline_no_trade_expected_value": no_trade,
        "baseline_always_allow_expected_value": always_allow,
        "baseline_random_expected_value": random_baseline,
        "beats_no_trade": selected_ev > no_trade,
        "beats_always_allow": selected_ev > always_allow,
        "beats_random": selected_ev > random_baseline,
        "overfit_warning": bool(precision5 == 1.0 and precision10 == 1.0 and len(test_frame) >= 10),
    }


def aggregate_metrics(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    if not metrics:
        return {
            "split_count": 0,
            "mean_rank_ic": 0.0,
            "mean_precision_at_10": 0.0,
            "selected_top_k_expected_value_total": 0.0,
            "beats_no_trade_split_count": 0,
            "beats_always_allow_split_count": 0,
            "beats_random_split_count": 0,
        }
    frame = pd.DataFrame(metrics)
    return {
        "split_count": int(len(metrics)),
        "mean_rank_ic": rounded_mean(frame["rank_ic"]),
        "mean_precision_at_10": rounded_mean(frame["precision_at_10"]),
        "selected_top_k_expected_value_total": rounded_sum(frame["selected_top_k_expected_value"]),
        "beats_no_trade_split_count": int(frame["beats_no_trade"].sum()),
        "beats_always_allow_split_count": int(frame["beats_always_allow"].sum()),
        "beats_random_split_count": int(frame["beats_random"].sum()),
    }


def baseline_comparison(metrics: list[dict[str, Any]], baseline_summary: dict[str, Any]) -> dict[str, Any]:
    aggregate = aggregate_metrics(metrics)
    return {
        "baseline_status": baseline_summary.get("baseline_status", "unknown"),
        "candidate_selected_top_k_expected_value_total": aggregate["selected_top_k_expected_value_total"],
        "baseline_no_trade_expected_value": baseline_summary.get("no_trade_expected_value", 0.0),
        "baseline_always_allow_expected_value": baseline_summary.get("always_allow_expected_value", 0.0),
        "baseline_random_expected_value": baseline_summary.get("random_deterministic_expected_value", 0.0),
        "beats_no_trade_split_count": aggregate["beats_no_trade_split_count"],
        "beats_always_allow_split_count": aggregate["beats_always_allow_split_count"],
        "beats_random_split_count": aggregate["beats_random_split_count"],
    }


def precision_at_k(label: pd.Series, pred: pd.Series, k: int) -> float:
    if label.empty:
        return 0.0
    selected = label.loc[pred.sort_values(ascending=False).head(min(k, len(label))).index]
    return round(float((selected > 0).mean()), 10)


def recall_at_k(label: pd.Series, pred: pd.Series, k: int) -> float:
    positives = int((label > 0).sum())
    if positives == 0:
        return 0.0
    selected = label.loc[pred.sort_values(ascending=False).head(min(k, len(label))).index]
    return round(float((selected > 0).sum() / positives), 10)


def decile_values(target: pd.Series, pred: pd.Series) -> tuple[float, float]:
    if target.empty:
        return 0.0, 0.0
    count = max(1, int(len(target) * 0.1))
    ordered = pred.sort_values(ascending=False)
    top = target.loc[ordered.head(count).index]
    bottom = target.loc[ordered.tail(count).index]
    return rounded_sum(top), rounded_sum(bottom)


def selected_top_k(frame: pd.DataFrame, pred: pd.Series, k: int) -> pd.DataFrame:
    if frame.empty:
        return frame
    return frame.loc[pred.sort_values(ascending=False).head(min(k, len(frame))).index]


def corr(left: pd.Series, right: pd.Series, method: str = "pearson") -> float:
    if len(left) < 2 or left.nunique(dropna=False) <= 1 or right.nunique(dropna=False) <= 1:
        return 0.0
    value = left.corr(right, method=method)
    return 0.0 if pd.isna(value) else round(float(value), 10)


def rounded_mean(series: pd.Series) -> float:
    if series.empty:
        return 0.0
    return round(float(pd.to_numeric(series, errors="coerce").fillna(0.0).mean()), 10)


def rounded_sum(series: pd.Series) -> float:
    if series.empty:
        return 0.0
    return round(float(pd.to_numeric(series, errors="coerce").fillna(0.0).sum()), 10)
