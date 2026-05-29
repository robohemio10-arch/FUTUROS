from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from smartcrypto.ml.sidecar_financial_evaluation import (
    SidecarFinancialEvaluationError,
    run_sidecar_financial_evaluation,
)


def feature_frame(rows: int = 24) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_id": [f"t{idx}" for idx in range(rows)],
            "symbol": ["BTCUSDT"] * rows,
            "open_1m_ts": pd.date_range("2026-01-01T00:00:00Z", periods=rows, freq="min"),
            "target_win": [idx % 2 for idx in range(rows)],
            "open_1m_ret": [idx / 1000 for idx in range(rows)],
            "open_5m_ret": [idx / 2000 for idx in range(rows)],
        }
    )


def sidecar_frame(rows: int = 24) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_id": [f"t{idx}" for idx in range(rows)],
            "symbol": ["BTCUSDT"] * rows,
            "open_1m_ts": pd.date_range("2026-01-01T00:00:00Z", periods=rows, freq="min"),
            "target_win": [idx % 2 for idx in range(rows)],
            "return_pct": [0.01 if idx % 2 else -0.005 for idx in range(rows)],
            "mfe_pct": [0.02] * rows,
            "mae_pct": [-0.01] * rows,
        }
    )


def test_evaluation_fails_if_return_pct_is_in_features() -> None:
    features = feature_frame().assign(return_pct=0.01)

    with pytest.raises(SidecarFinancialEvaluationError, match="outcome_or_close"):
        run_sidecar_financial_evaluation(
            features,
            sidecar_frame(),
            features_path="features.parquet",
            sidecar_path="sidecar.parquet",
        )


def test_evaluation_fails_if_close_feature_is_in_features() -> None:
    features = feature_frame().assign(close_1m_ret=0.01)

    with pytest.raises(SidecarFinancialEvaluationError, match="outcome_or_close"):
        run_sidecar_financial_evaluation(
            features,
            sidecar_frame(),
            features_path="features.parquet",
            sidecar_path="sidecar.parquet",
        )


def test_evaluation_uses_return_pct_only_from_sidecar_and_preserves_join_rows() -> None:
    report = run_sidecar_financial_evaluation(
        feature_frame(),
        sidecar_frame(),
        features_path="features.parquet",
        sidecar_path="sidecar.parquet",
        folds=2,
        embargo_minutes=1,
    )

    assert report.status == "OK"
    assert report.rows == 24
    assert report.joined_rows == 24
    assert report.return_column == "return_pct"


def test_random_baseline_is_reproducible_with_seed() -> None:
    first = run_sidecar_financial_evaluation(
        feature_frame(),
        sidecar_frame(),
        features_path="features.parquet",
        sidecar_path="sidecar.parquet",
        folds=2,
        embargo_minutes=1,
        seed=123,
    )
    second = run_sidecar_financial_evaluation(
        feature_frame(),
        sidecar_frame(),
        features_path="features.parquet",
        sidecar_path="sidecar.parquet",
        folds=2,
        embargo_minutes=1,
        seed=123,
    )

    assert first.aggregate_metrics["random_strategy"] == second.aggregate_metrics["random_strategy"]


def test_majority_and_no_trade_metrics_are_generated() -> None:
    report = run_sidecar_financial_evaluation(
        feature_frame(),
        sidecar_frame(),
        features_path="features.parquet",
        sidecar_path="sidecar.parquet",
        folds=2,
        embargo_minutes=1,
    )

    assert "majority_class" in report.aggregate_metrics
    assert report.aggregate_metrics["no_trade/cash"]["total_return_pct"] == 0.0
    assert report.aggregate_metrics["no_trade/cash"]["trades"] == 0.0


def test_financial_metrics_are_json_serializable_and_folded() -> None:
    report = run_sidecar_financial_evaluation(
        feature_frame(),
        sidecar_frame(),
        features_path="features.parquet",
        sidecar_path="sidecar.parquet",
        folds=2,
        embargo_minutes=1,
    )

    payload = report.to_dict()
    assert len(payload["fold_metrics"]) == 2
    assert json.dumps(payload, sort_keys=True)


def test_evaluation_fails_if_join_loses_rows() -> None:
    sidecar = sidecar_frame().iloc[:-1].copy()

    with pytest.raises(SidecarFinancialEvaluationError, match="sidecar_join_lost_rows"):
        run_sidecar_financial_evaluation(
            feature_frame(),
            sidecar,
            features_path="features.parquet",
            sidecar_path="sidecar.parquet",
            folds=2,
            embargo_minutes=1,
        )


def test_evaluation_modules_do_not_reference_exchange_or_live_flags() -> None:
    text = "\n".join(
        [
            Path("smartcrypto/ml/sidecar_financial_evaluation.py").read_text(encoding="utf-8"),
            Path("scripts/run_sidecar_financial_evaluation.py").read_text(encoding="utf-8"),
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
