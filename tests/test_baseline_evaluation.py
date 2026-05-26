from __future__ import annotations

import json

import pandas as pd

from smartcrypto.ml.baseline_evaluation import evaluate_baselines


def baseline_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "target_win": [1, 0, 1, 1, 0, 0],
            "return_pct": [0.03, -0.01, 0.02, 0.01, -0.02, -0.01],
            "fee_pct": [0.001] * 6,
            "feature": [1, 2, 3, 4, 5, 6],
        }
    )


def test_random_baseline_is_reproducible_with_seed() -> None:
    first = evaluate_baselines(baseline_frame(), seed=123).to_dict()
    second = evaluate_baselines(baseline_frame(), seed=123).to_dict()

    assert first["baselines"]["random_strategy"] == second["baselines"]["random_strategy"]


def test_majority_class_runs_without_real_order_side_effects() -> None:
    result = evaluate_baselines(baseline_frame())

    assert "majority_class" in result.baselines
    assert result.baselines["majority_class"]["trades"] >= 0


def test_metrics_are_json_serializable() -> None:
    payload = evaluate_baselines(baseline_frame()).to_dict()

    encoded = json.dumps(payload, sort_keys=True)

    assert "profit_factor" in encoded
    assert payload["limitations"] == []


def test_report_marks_missing_cost_columns_as_limitation() -> None:
    frame = baseline_frame().drop(columns=["fee_pct"])

    report = evaluate_baselines(frame)

    assert "no_cost_slippage_or_spread_columns_present" in report.limitations
