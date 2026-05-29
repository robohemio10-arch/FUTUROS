from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from smartcrypto.ml.normalized_financial_evaluation import (
    NormalizedFinancialEvaluationError,
    run_normalized_financial_evaluation,
)


def features(rows: int = 24) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_id": [f"t{idx}" for idx in range(rows)],
            "symbol": ["BTCUSDT"] * rows,
            "open_1m_ts": pd.date_range("2026-01-01T00:00:00Z", periods=rows, freq="min"),
            "target_win": [idx % 2 for idx in range(rows)],
            "open_1m_ret": [idx / 1000 for idx in range(rows)],
        }
    )


def sidecar(rows: int = 24) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_id": [f"t{idx}" for idx in range(rows)],
            "target_win": [idx % 2 for idx in range(rows)],
            "net_return_pct": [0.5 if idx % 2 else -0.2 for idx in range(rows)],
        }
    )


def sidecar_report(status: str = "OK", rows: int | None = None) -> dict:
    payload = {
        "status": status,
        "quality_flag_counts": {"net_return_extreme": 1} if status == "BLOCKED" else {},
        "outlier_summary": {"net_return_extreme": 1} if status == "BLOCKED" else {},
        "recommended_next_action": (
            "block_normalized_financial_metrics_until_required_price_side_inputs_are_repaired"
            if status == "BLOCKED"
            else "normalized_returns_plausible_for_offline_research_only"
        ),
    }
    if rows is not None:
        payload["rows"] = rows
    return payload


def test_evaluation_fails_if_features_contain_financial_columns() -> None:
    for column in ("net_return_pct", "return_pct"):
        with pytest.raises(NormalizedFinancialEvaluationError, match="forbidden_financial"):
            run_normalized_financial_evaluation(
                features().assign(**{column: 0.1}),
                sidecar(),
                features_path="features.parquet",
                sidecar_path="sidecar.parquet",
                sidecar_report=sidecar_report(),
            )


def test_evaluation_fails_if_features_contain_close_columns() -> None:
    with pytest.raises(NormalizedFinancialEvaluationError, match="forbidden_financial"):
        run_normalized_financial_evaluation(
            features().assign(close_1m_ret=0.1),
            sidecar(),
            features_path="features.parquet",
            sidecar_path="sidecar.parquet",
            sidecar_report=sidecar_report(),
        )


def test_uses_net_return_only_from_sidecar_and_preserves_rows() -> None:
    report = run_normalized_financial_evaluation(
        features(),
        sidecar(),
        features_path="features.parquet",
        sidecar_path="sidecar.parquet",
        folds=2,
        embargo_minutes=1,
        sidecar_report=sidecar_report(),
    )

    assert report["status"] == "OK"
    assert report["joined_rows"] == 24
    assert report["return_column"] == "net_return_pct"
    assert report["return_semantics"] == "normalized_net_return_pct"


def test_random_baseline_is_reproducible_and_majority_has_net_metrics() -> None:
    first = run_normalized_financial_evaluation(
        features(),
        sidecar(),
        features_path="features.parquet",
        sidecar_path="sidecar.parquet",
        folds=2,
        embargo_minutes=1,
        seed=123,
        sidecar_report=sidecar_report(),
    )
    second = run_normalized_financial_evaluation(
        features(),
        sidecar(),
        features_path="features.parquet",
        sidecar_path="sidecar.parquet",
        folds=2,
        embargo_minutes=1,
        seed=123,
        sidecar_report=sidecar_report(),
    )

    assert first["aggregate_metrics"]["random_strategy"] == second["aggregate_metrics"]["random_strategy"]
    assert "average_net_return_pct" in first["aggregate_metrics"]["majority_class"]


def test_no_trade_cash_returns_zero() -> None:
    report = run_normalized_financial_evaluation(
        features(),
        sidecar(),
        features_path="features.parquet",
        sidecar_path="sidecar.parquet",
        folds=2,
        embargo_minutes=1,
        sidecar_report=sidecar_report(),
    )

    assert report["aggregate_metrics"]["no_trade/cash"]["total_net_return_pct"] == 0.0
    assert report["aggregate_metrics"]["no_trade/cash"]["trades"] == 0.0
    assert len(report["fold_metrics"]) == 2
    assert json.dumps(report, sort_keys=True)


def test_join_loss_is_blocked() -> None:
    with pytest.raises(NormalizedFinancialEvaluationError, match="sidecar_join_lost_rows"):
        run_normalized_financial_evaluation(
            features(),
            sidecar().iloc[:-1].copy(),
            features_path="features.parquet",
            sidecar_path="sidecar.parquet",
            folds=2,
            embargo_minutes=1,
            sidecar_report=sidecar_report(),
        )


def test_finance_grade_filtered_sidecar_can_evaluate_matching_subset() -> None:
    filtered_sidecar = sidecar().iloc[:-3].copy()
    report = run_normalized_financial_evaluation(
        features(),
        filtered_sidecar,
        features_path="features.parquet",
        sidecar_path="sidecar.parquet",
        folds=2,
        embargo_minutes=1,
        sidecar_report=sidecar_report("OK", rows=len(filtered_sidecar)),
    )

    assert report["status"] == "OK"
    assert report["joined_rows"] == len(filtered_sidecar)
    assert report["original_feature_rows"] == 24
    assert report["finance_grade_excluded_rows"] == 3
    assert "features_filtered_to_finance_grade_sidecar" in report["limitations"]


def test_evaluation_returns_blocked_when_sidecar_report_is_blocked() -> None:
    report = run_normalized_financial_evaluation(
        features(),
        sidecar(),
        features_path="features.parquet",
        sidecar_path="sidecar.parquet",
        sidecar_report=sidecar_report("BLOCKED"),
    )

    assert report["status"] == "BLOCKED"
    assert report["reason"] == "normalized_return_sidecar_blocked"
    assert report["aggregate_metrics"] == {}
    assert report["recommended_next_action"] == (
        "block_normalized_financial_metrics_until_required_price_side_inputs_are_repaired"
    )


def test_evaluation_accepts_ok_or_warning_sidecar_report() -> None:
    ok = run_normalized_financial_evaluation(
        features(),
        sidecar(),
        features_path="features.parquet",
        sidecar_path="sidecar.parquet",
        sidecar_report=sidecar_report("OK"),
        folds=2,
        embargo_minutes=1,
    )
    warning = run_normalized_financial_evaluation(
        features(),
        sidecar(),
        features_path="features.parquet",
        sidecar_path="sidecar.parquet",
        sidecar_report=sidecar_report("WARNING"),
        folds=2,
        embargo_minutes=1,
    )

    assert ok["status"] == "OK"
    assert warning["status"] == "WARNING"
    assert warning["sidecar_status"] == "WARNING"


def test_allow_blocked_sidecar_runs_as_diagnostic_warning() -> None:
    report = run_normalized_financial_evaluation(
        features(),
        sidecar(),
        features_path="features.parquet",
        sidecar_path="sidecar.parquet",
        sidecar_report=sidecar_report("BLOCKED"),
        allow_blocked_sidecar=True,
        folds=2,
        embargo_minutes=1,
    )

    assert report["status"] == "WARNING"
    assert "blocked_sidecar_allowed_for_diagnostic_run" in report["limitations"]
    assert report["aggregate_metrics"]
    assert json.dumps(report, sort_keys=True)


def test_missing_sidecar_report_is_warning_for_tmp_path_tests() -> None:
    report = run_normalized_financial_evaluation(
        features(),
        sidecar(),
        features_path="features.parquet",
        sidecar_path="sidecar.parquet",
        sidecar_report=None,
        folds=2,
        embargo_minutes=1,
    )

    assert report["status"] == "WARNING"
    assert "sidecar_report_missing_or_not_provided" in report["limitations"]


def test_modules_do_not_reference_exchange_or_live_flags() -> None:
    text = "\n".join(
        [
            Path("smartcrypto/ml/normalized_financial_evaluation.py").read_text(encoding="utf-8"),
            Path("scripts/run_normalized_financial_evaluation.py").read_text(encoding="utf-8"),
        ]
    )
    forbidden = [
        "ccxt",
        "create_order",
        "submit_order",
        "fetch_balance",
        "LIVE_ENABLED=true",
        "ORDER_SUBMISSION_ENABLED=true",
        "REAL_ORDER_SUBMISSION_ENABLED=true",
    ]
    assert all(token not in text for token in forbidden)
