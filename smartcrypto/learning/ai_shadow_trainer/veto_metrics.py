"""Metrics for research-only AI Shadow quality veto challengers."""

from __future__ import annotations

from typing import Any

import pandas as pd


def quality_label(frame: pd.DataFrame) -> pd.Series:
    expected_value = pd.to_numeric(frame.get("target_expected_value_component", 0.0), errors="coerce").fillna(0.0)
    stoploss = frame.get("target_stoploss_hit", False)
    stoploss_hit = pd.Series(stoploss, index=frame.index).fillna(False).astype(bool)
    return ((expected_value > 0.0) & (~stoploss_hit)).astype(int)


def split_metrics(
    *,
    split_id: str,
    train_row_count: int,
    validation_row_count: int,
    test_frame: pd.DataFrame,
    probability_quality: pd.Series,
    decision: pd.Series,
) -> dict[str, Any]:
    from sklearn.metrics import brier_score_loss, precision_score, recall_score, roc_auc_score

    label = quality_label(test_frame)
    reject_true = (label == 0).astype(int)
    reject_pred = (decision == "AI_REJECT").astype(int)
    expected_value = pd.to_numeric(test_frame.get("target_expected_value_component", 0.0), errors="coerce").fillna(0.0)
    accepted = expected_value.loc[decision[decision == "AI_ACCEPT"].index]
    rejected = expected_value.loc[decision[decision == "AI_REJECT"].index]
    rejected_losses = rejected[rejected <= 0]
    accepted_profits = accepted[accepted > 0]
    false_rejects = ((decision == "AI_REJECT") & (label == 1)).sum()
    false_accepts = ((decision == "AI_ACCEPT") & (label == 0)).sum()
    quality_auc = None
    quality_brier = None
    if label.nunique(dropna=False) >= 2 and probability_quality.nunique(dropna=False) >= 2:
        quality_auc = round(float(roc_auc_score(label, probability_quality)), 10)
        quality_brier = round(float(brier_score_loss(label, probability_quality)), 10)
    accepted_ev = rounded_sum(accepted)
    always_accept_ev = rounded_sum(expected_value)
    return {
        "split_id": split_id,
        "train_row_count": int(train_row_count),
        "validation_row_count": int(validation_row_count),
        "test_row_count": int(len(test_frame)),
        "precision_reject": round(float(precision_score(reject_true, reject_pred, zero_division=0)), 10) if len(label) else 0.0,
        "recall_reject": round(float(recall_score(reject_true, reject_pred, zero_division=0)), 10) if len(label) else 0.0,
        "false_reject_rate": round(float(false_rejects / max(1, int((label == 1).sum()))), 10),
        "false_accept_rate": round(float(false_accepts / max(1, int((label == 0).sum()))), 10),
        "rejected_expected_value": rounded_sum(rejected),
        "accepted_expected_value": accepted_ev,
        "net_ev_delta_if_applied_research_only": round(accepted_ev - always_accept_ev, 10),
        "avoided_loss_count": int(len(rejected_losses)),
        "missed_profit_count": int(false_rejects),
        "quality_auc": quality_auc,
        "quality_brier_score": quality_brier,
        "threshold": 0.5,
        "threshold_scope": "symbol_side_regime",
        "beats_always_accept": accepted_ev > always_accept_ev,
        "beats_always_reject": accepted_ev > 0.0,
        "overfit_warning": bool(quality_auc == 1.0 and len(test_frame) >= 10),
    }


def aggregate_metrics(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    if not metrics:
        return {
            "split_count": 0,
            "mean_precision_reject": 0.0,
            "mean_recall_reject": 0.0,
            "mean_false_reject_rate": 0.0,
            "mean_false_accept_rate": 0.0,
            "accepted_expected_value_total": 0.0,
            "rejected_expected_value_total": 0.0,
            "net_ev_delta_if_applied_research_only_total": 0.0,
            "avoided_loss_count_total": 0,
            "missed_profit_count_total": 0,
        }
    frame = pd.DataFrame(metrics)
    return {
        "split_count": int(len(metrics)),
        "mean_precision_reject": rounded_mean(frame["precision_reject"]),
        "mean_recall_reject": rounded_mean(frame["recall_reject"]),
        "mean_false_reject_rate": rounded_mean(frame["false_reject_rate"]),
        "mean_false_accept_rate": rounded_mean(frame["false_accept_rate"]),
        "accepted_expected_value_total": rounded_sum(frame["accepted_expected_value"]),
        "rejected_expected_value_total": rounded_sum(frame["rejected_expected_value"]),
        "net_ev_delta_if_applied_research_only_total": rounded_sum(frame["net_ev_delta_if_applied_research_only"]),
        "avoided_loss_count_total": int(frame["avoided_loss_count"].sum()),
        "missed_profit_count_total": int(frame["missed_profit_count"].sum()),
    }


def baseline_comparison(metrics: list[dict[str, Any]], baseline_summary: dict[str, Any]) -> dict[str, Any]:
    aggregate = aggregate_metrics(metrics)
    return {
        "always_accept_expected_value": float(baseline_summary.get("always_allow_expected_value", 0.0) or 0.0),
        "always_reject_expected_value": 0.0,
        "random_deterministic_expected_value": float(baseline_summary.get("random_deterministic_expected_value", 0.0) or 0.0),
        "candidate_accepted_expected_value_total": aggregate["accepted_expected_value_total"],
        "beats_always_accept_split_count": int(sum(1 for metric in metrics if metric.get("beats_always_accept"))),
        "beats_always_reject_split_count": int(sum(1 for metric in metrics if metric.get("beats_always_reject"))),
    }


def rounded_mean(series: pd.Series) -> float:
    if series.empty:
        return 0.0
    return round(float(pd.to_numeric(series, errors="coerce").fillna(0.0).mean()), 10)


def rounded_sum(series: pd.Series) -> float:
    if series.empty:
        return 0.0
    return round(float(pd.to_numeric(series, errors="coerce").fillna(0.0).sum()), 10)
