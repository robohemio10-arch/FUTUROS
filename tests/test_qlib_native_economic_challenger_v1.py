from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from smartcrypto.learning.paper_autolearning.qlib_native_economic_challenger import (
    FOLD_COUNT,
    build_qlib_native_economic_challenger_v1,
)


def _rows(count: int = 360) -> list[dict[str, object]]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    rows: list[dict[str, object]] = []
    notionals = (100.0, 200.0, 300.0, 400.0)
    for index in range(count):
        notional = notionals[index % len(notionals)]
        close_time = base + timedelta(hours=index + 1)
        open_time = close_time - timedelta(hours=1)
        rows.append(
            {
                "event_id": f"event-{index:04d}",
                "trade_id": f"trade-{index:04d}",
                "order_id": f"order-{index:04d}",
                "is_closed": True,
                "validation_status": "ok",
                "symbol_norm": "BTCUSDT" if index % 2 == 0 else "ETHUSDT",
                "side": "long" if index % 2 == 0 else "short",
                "open_time_utc": open_time.isoformat(),
                "close_time_utc": close_time.isoformat(),
                "entry_price": 1.0,
                "quantity": notional,
                "notional": notional,
                "leverage": 1.0 + float(index % 3),
                "paper_candidate_filter_called": bool(index % 2),
                "net_pnl": 3.0 if notional >= 300.0 else -1.0,
                "trading_fee": 0.0,
                "funding_fee": 0.0,
            }
        )
    return rows


def _profitable_predictor(
    train_x: pd.DataFrame,
    train_y: pd.Series,
    calibration_x: pd.DataFrame,
    test_x: pd.DataFrame,
    *,
    fold_id: str,
) -> tuple[np.ndarray, np.ndarray]:
    del train_x, train_y, fold_id
    return (
        calibration_x["feature_notional"].to_numpy(dtype=float),
        test_x["feature_notional"].to_numpy(dtype=float),
    )


def _losing_predictor(
    train_x: pd.DataFrame,
    train_y: pd.Series,
    calibration_x: pd.DataFrame,
    test_x: pd.DataFrame,
    *,
    fold_id: str,
) -> tuple[np.ndarray, np.ndarray]:
    del train_x, train_y, fold_id
    return (
        -calibration_x["feature_notional"].to_numpy(dtype=float),
        -test_x["feature_notional"].to_numpy(dtype=float),
    )


def test_test_double_proves_profit_path_without_claiming_native_qlib() -> None:
    report = build_qlib_native_economic_challenger_v1(
        project_root=Path("."),
        rows=_rows(),
        additional_execution_stress_bps=0.0,
        predictor=_profitable_predictor,
    )

    assert report["status"] == "ok"
    assert report["decision"] == "TEST_DOUBLE_ECONOMIC_PATH_VALIDATED"
    assert report["predictor_mode"] == "injected_test_double"
    assert report["native_qlib_used"] is False
    assert report["model"]["fallback_used"] is False
    assert report["oos_selected_trade_count"] > 0
    assert len(report["folds"]) == FOLD_COUNT
    assert all(fold["status"] == "ok" for fold in report["folds"])
    assert all(fold["chronological_boundary_valid"] for fold in report["folds"])

    economic = report["economic_evaluation"]
    assert economic["status"] == "ok"
    assert economic["aggregate_treatment_metrics"]["stressed_net_pnl_total"] > 0
    assert economic["aggregate_delta_stressed_net_pnl"] > 0
    assert economic["positive_treatment_net_pnl_fold_count"] == FOLD_COUNT


def test_unprofitable_calibration_is_fail_closed() -> None:
    report = build_qlib_native_economic_challenger_v1(
        project_root=Path("."),
        rows=_rows(),
        additional_execution_stress_bps=0.0,
        predictor=_losing_predictor,
    )

    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert "one_or_more_model_folds_blocked" in report["blockers"]
    assert all(fold["status"] == "blocked" for fold in report["folds"])
    assert all(
        fold["reason"].endswith("calibration_no_profitable_threshold")
        for fold in report["folds"]
    )


def test_insufficient_history_blocks_before_model_execution() -> None:
    called = False

    def predictor(
        train_x: pd.DataFrame,
        train_y: pd.Series,
        calibration_x: pd.DataFrame,
        test_x: pd.DataFrame,
        *,
        fold_id: str,
    ) -> tuple[np.ndarray, np.ndarray]:
        nonlocal called
        called = True
        del train_x, train_y, calibration_x, test_x, fold_id
        return np.asarray([]), np.asarray([])

    report = build_qlib_native_economic_challenger_v1(
        project_root=Path("."),
        rows=_rows(120),
        predictor=predictor,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "min_total_trades_not_met"
    assert called is False
    assert report["native_qlib_used"] is False


def test_prediction_shape_mismatch_blocks_fold() -> None:
    def bad_predictor(
        train_x: pd.DataFrame,
        train_y: pd.Series,
        calibration_x: pd.DataFrame,
        test_x: pd.DataFrame,
        *,
        fold_id: str,
    ) -> tuple[np.ndarray, np.ndarray]:
        del train_x, train_y, fold_id
        return (
            np.zeros(max(0, len(calibration_x) - 1), dtype=float),
            np.zeros(len(test_x), dtype=float),
        )

    report = build_qlib_native_economic_challenger_v1(
        project_root=Path("."),
        rows=_rows(),
        predictor=bad_predictor,
        additional_execution_stress_bps=0.0,
    )

    assert report["status"] == "blocked"
    assert any("predictor_failed:ValueError" in blocker for blocker in report["blockers"])


def test_invalid_close_time_is_fail_closed() -> None:
    rows = _rows()
    rows[10]["close_time_utc"] = "not-a-timestamp"

    report = build_qlib_native_economic_challenger_v1(
        project_root=Path("."),
        rows=rows,
        predictor=_profitable_predictor,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "invalid_close_time_detected"
    assert report["invalid_close_time_count"] == 1


def test_safety_contract_never_grants_operational_authority() -> None:
    report = build_qlib_native_economic_challenger_v1(
        project_root=Path("."),
        rows=_rows(),
        additional_execution_stress_bps=0.0,
        predictor=_profitable_predictor,
    )

    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["research_only"] is True
    assert report["operational_authority"] is False
    assert report["promotion_allowed"] is False
    assert report["model_promotion_performed"] is False
    assert report["active_model_changed"] is False
    assert report["updates_qlib_runtime"] is False
    assert report["updates_ai_shadow_runtime"] is False
    assert report["changes_risk"] is False
    assert report["sends_orders"] is False
    assert report["exchange_private_access"] is False
    assert report["writes_runtime"] is False
    assert report["writes_sqlite"] is False
    assert report["writes_parquet"] is False
    assert report["write_performed"] is False
