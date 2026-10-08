from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from smartcrypto.research.aibot_parity import br10_profit_recalibration as recalibration


class _ScoreModel:
    def fit(self, x: np.ndarray, y: np.ndarray) -> _ScoreModel:
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        return x[:, 0]


def _factory() -> _ScoreModel:
    return _ScoreModel()


def _dataset() -> tuple[pd.DataFrame, list[dict[str, object]]]:
    records: list[dict[str, object]] = []
    for index in range(130):
        if index < 10:
            period = 0
        elif index < 110:
            period = 10
        else:
            period = 20
        opened = pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(days=period, minutes=index)
        symbol = "BTCUSDT" if index % 2 == 0 else "ETHUSDT"
        score = 0.1 if (index // 2) % 2 == 0 else 0.9
        pnl = (10.0 if score == 0.1 else -2.0) if symbol == "BTCUSDT" else (
            -10.0 if score == 0.1 else 2.0
        )
        records.append(
            {
                "trade_sequence": index,
                "symbol": symbol,
                "side": "long",
                "open_time_utc": opened,
                "close_time_utc": opened + pd.Timedelta(hours=1),
                "feature_cutoff_utc": opened - pd.Timedelta(minutes=1),
                "score_feature": score,
                "feature_ret_close_30m": score,
                "feature_pre_entry_volatility_20": score,
                "feature_rsi_14": score,
                "label_economic_net_pnl": pnl,
                "capital_proxy_usdt": 100.0,
                "capital_hours": 100.0,
            }
        )
    split: dict[str, object] = {
        "split_id": "fold-1",
        "_train_indices": list(range(10)),
        "_validation_indices": list(range(10, 110)),
        "_test_indices": list(range(110, 130)),
    }
    return pd.DataFrame.from_records(records), [split]


def _evaluate(dataset: pd.DataFrame, splits: list[dict[str, object]]) -> dict[str, object]:
    return recalibration.evaluate_profit_recalibration_oos(
        dataset=dataset,
        features=("score_feature",),
        splits=splits,
        model_factory=_factory,
    )


def test_group_calibration_uses_only_validation_labels_and_pretrade_group() -> None:
    dataset, splits = _dataset()
    report = _evaluate(dataset, splits)

    assert report["anti_leakage_status"] == "PASS"
    assert report["outcome_used_for_selection"] is False
    fold = report["folds"][0]
    assert fold["recalibrated_thresholds_by_symbol_side"]["BTCUSDT|long"] == 0.1
    assert 0.1 < fold["recalibrated_thresholds_by_symbol_side"]["ETHUSDT|long"] <= 0.9
    assert report["abstention_attribution"]["regime"]["status"] == "UNAVAILABLE"
    assert report["abstention_attribution"]["confidence"]["status"] == "UNAVAILABLE"
    assert report["br10_recalibrated"]["trade_count"] > 0
    assert report["economic_gate_pass"] is (
        report["br10_recalibrated_net_pnl_usdt"] > report["control_net_pnl_usdt"]
    )


def test_heldout_outcome_changes_metrics_but_not_threshold_or_admission() -> None:
    dataset, splits = _dataset()
    baseline = _evaluate(dataset, splits)
    changed = dataset.copy()
    changed.loc[110:, "label_economic_net_pnl"] *= -100.0
    report = _evaluate(changed, splits)

    assert baseline["folds"][0]["current_threshold"] == report["folds"][0]["current_threshold"]
    assert baseline["folds"][0]["recalibrated_thresholds_by_symbol_side"] == report["folds"][0]["recalibrated_thresholds_by_symbol_side"]
    assert baseline["br10_current"]["trade_count"] == report["br10_current"]["trade_count"]
    assert baseline["br10_recalibrated"]["trade_count"] == report["br10_recalibrated"]["trade_count"]
    assert baseline["br10_recalibrated_net_pnl_usdt"] != report["br10_recalibrated_net_pnl_usdt"]


def test_fails_closed_on_future_validation_outcome_and_feature() -> None:
    dataset, splits = _dataset()
    late = dataset.copy()
    late.loc[10, "close_time_utc"] = pd.Timestamp("2026-02-01T00:00:00Z")
    with pytest.raises(recalibration.RecalibrationError, match="validation_outcome_after_test_start"):
        _evaluate(late, splits)

    feature = dataset.copy()
    feature.loc[110, "feature_cutoff_utc"] = feature.loc[110, "open_time_utc"] + pd.Timedelta(seconds=1)
    with pytest.raises(recalibration.RecalibrationError, match="feature_available_after_decision"):
        _evaluate(feature, splits)


def test_unseen_symbol_side_group_fails_closed() -> None:
    dataset, splits = _dataset()
    dataset.loc[110, "side"] = "short"
    with pytest.raises(recalibration.RecalibrationError, match="unseen_pretrade_group"):
        _evaluate(dataset, splits)


def test_economic_gate_fails_when_recalibration_does_not_beat_control() -> None:
    dataset, splits = _dataset()
    dataset.loc[110:, "label_economic_net_pnl"] = 1.0
    report = _evaluate(dataset, splits)

    assert report["economic_gate_pass"] is False
    assert report["decision"] == "BR10_RECALIBRATE_FAILED"
    assert report["anti_leakage_status"] == "PASS"


def test_group_below_minimum_uses_existing_global_threshold() -> None:
    dataset, splits = _dataset()
    validation = dataset.iloc[splits[0]["_validation_indices"]].copy()
    validation.loc[validation.index[:30], "symbol"] = "XRPUSDT"
    scores = validation["score_feature"].to_numpy(dtype=float)
    thresholds = recalibration._calibrate_groups(validation, scores, 0.42)
    assert thresholds["XRPUSDT|long"] == 0.42
