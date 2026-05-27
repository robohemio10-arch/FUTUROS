from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from smartcrypto.ml.model_vs_baseline_financial_evaluation import (
    BLOCKED,
    WARNING,
    run_model_vs_baseline_financial_evaluation,
    select_feature_columns,
)


MODULE_PATH = Path("scripts/run_model_vs_baseline_financial_evaluation.py")


def feature_frame(rows: int = 360) -> pd.DataFrame:
    idx = np.arange(rows)
    target = (idx % 3 != 0).astype(int)
    return pd.DataFrame(
        {
            "trade_id": [f"t{item}" for item in idx],
            "symbol": ["BTCUSDT" if item % 2 else "ETHUSDT" for item in idx],
            "open_1m_ts": pd.date_range("2026-01-01T00:00:00Z", periods=rows, freq="min"),
            "open_5m_ts": pd.date_range("2026-01-01T00:00:00Z", periods=rows, freq="5min"),
            "target_win": target,
            "open_1m_ret_1": target + (idx % 5) / 100.0,
            "open_1m_rsi_14": 40 + (idx % 30),
            "open_5m_ret_1": target * 0.5 + (idx % 7) / 100.0,
            "close_1m_ret_1": idx / 10.0,
            "return_pct": idx / 100.0,
            "net_return_pct": idx / 100.0,
            "pnl": idx / 10.0,
            "raw_return_resolved": idx / 10.0,
            "exit_price_repaired": 100 + idx,
            "mfe_pct": idx / 100.0,
            "mae_pct": idx / 100.0,
            "path_candles": ["[]"] * rows,
        }
    )


def sidecar_frame(rows: int = 360, *, all_positive: bool = False) -> pd.DataFrame:
    idx = np.arange(rows)
    target = (idx % 3 != 0).astype(int)
    returns = np.full(rows, 0.1) if all_positive else np.where(target == 1, 0.8, -0.25)
    return pd.DataFrame(
        {
            "trade_id": [f"t{item}" for item in idx],
            "target_win": target,
            "net_return_pct": returns,
        }
    )


def sidecar_report(status: str = "OK", rows: int = 360) -> dict:
    return {
        "status": status,
        "rows": rows,
        "quality_flag_counts": {"net_return_extreme": 1} if status == BLOCKED else {},
        "outlier_summary": {"net_return_extreme": 1} if status == BLOCKED else {},
        "recommended_next_action": "normalized_returns_plausible_for_offline_research_only",
    }


def run_eval(**overrides):
    kwargs = {
        "features": feature_frame(),
        "sidecar": sidecar_frame(),
        "features_path": "features.parquet",
        "sidecar_path": "sidecar.parquet",
        "sidecar_report": sidecar_report(),
        "folds": 3,
        "embargo_minutes": 1,
        "seed": 7,
        "min_train_rows": 40,
        "min_test_rows": 40,
        "probability_thresholds": "0.50,0.60",
    }
    kwargs.update(overrides)
    return run_model_vs_baseline_financial_evaluation(**kwargs)


def test_blocks_if_sidecar_report_is_blocked() -> None:
    report = run_eval(sidecar_report=sidecar_report(BLOCKED))

    assert report["status"] == BLOCKED
    assert report["reason"] == "normalized_return_sidecar_blocked"
    assert report["aggregate_metrics"] == {}


def test_joins_by_trade_id_and_excludes_rows_without_finance_grade_sidecar() -> None:
    sidecar = sidecar_frame().iloc[:-12].copy()
    report = run_eval(sidecar=sidecar, sidecar_report=sidecar_report(rows=len(sidecar)))

    assert report["joined_rows"] == len(sidecar)
    assert report["finance_grade_excluded_rows"] == 12
    assert "features_filtered_to_finance_grade_sidecar" in report["limitations"]


def test_select_feature_columns_removes_forbidden_columns_and_uses_open_features() -> None:
    selected, excluded = select_feature_columns(feature_frame(), id_column="trade_id", time_column="open_1m_ts")

    assert "open_1m_ret_1" in selected
    assert "open_5m_ret_1" in selected
    assert "target_win" in excluded
    assert "return_pct" in excluded
    assert "net_return_pct" in excluded
    assert "close_1m_ret_1" in excluded
    assert "path_candles" in excluded
    assert all(column.startswith(("open_1m_", "open_5m_")) for column in selected)


def test_generates_walkforward_folds_and_respects_embargo() -> None:
    report = run_eval()

    assert report["fold_metrics"]
    for fold in report["fold_metrics"]:
        train_end = pd.Timestamp(fold["train_end"])
        test_start = pd.Timestamp(fold["test_start"])
        assert train_end < test_start
        assert fold["embargo_seconds"] == 60


def test_trains_models_and_calculates_threshold_financial_metrics() -> None:
    report = run_eval()
    first_fold = report["fold_metrics"][0]

    assert "logistic_regression" in first_fold["model_metrics"]
    metrics = first_fold["model_metrics"]["logistic_regression"]["0.50"]
    assert "average_net_return_pct" in metrics
    assert "total_net_return_pct" in metrics
    assert "profit_factor" in metrics
    assert "max_drawdown" in metrics
    assert metrics["trades"] >= 0


def test_compares_models_against_baselines_and_generates_ranking() -> None:
    report = run_eval()

    assert "always_predict_win" in report["baseline_metrics"]
    assert "random_strategy" in report["baseline_metrics"]
    assert report["model_ranking"]
    assert report["best_model"] in {"logistic_regression", "random_forest", "gradient_boosting"}
    assert report["best_threshold"] in {0.5, 0.6}


def test_returns_warning_when_model_does_not_beat_baseline() -> None:
    report = run_eval(
        sidecar=sidecar_frame(all_positive=True),
        sidecar_report=sidecar_report(),
    )

    assert report["status"] == WARNING
    assert report["model_beats_baseline"] is False
    assert "no_model_beat_best_baseline_with_minimal_robustness" in report["limitations"]


def test_report_is_json_serializable() -> None:
    report = run_eval()

    assert json.dumps(report, sort_keys=True)


def test_runner_accepts_tmp_path_without_data_writes(tmp_path, monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("run_model_vs_baseline_financial_evaluation", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    paths = {
        str(tmp_path / "features.parquet"): feature_frame(),
        str(tmp_path / "sidecar.parquet"): sidecar_frame(),
    }
    monkeypatch.setattr(module, "read_parquet", lambda path: paths[str(path)])
    sidecar_report_path = tmp_path / "sidecar_report.json"
    sidecar_report_path.write_text(json.dumps(sidecar_report()), encoding="utf-8")

    rc = module.main(
        [
            "--features",
            str(tmp_path / "features.parquet"),
            "--sidecar",
            str(tmp_path / "sidecar.parquet"),
            "--sidecar-report",
            str(sidecar_report_path),
            "--output-report",
            str(tmp_path / "report.json"),
            "--folds",
            "3",
            "--embargo-minutes",
            "1",
            "--min-train-rows",
            "40",
            "--min-test-rows",
            "40",
            "--probability-thresholds",
            "0.50",
        ]
    )

    assert rc == 0
    assert (tmp_path / "report.json").exists()
    assert not (tmp_path / "data").exists()


def test_modules_do_not_reference_exchange_or_live_flags() -> None:
    text = "\n".join(
        [
            Path("smartcrypto/ml/model_vs_baseline_financial_evaluation.py").read_text(encoding="utf-8"),
            MODULE_PATH.read_text(encoding="utf-8"),
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
