from __future__ import annotations

import pandas as pd

from smartcrypto.ml.anti_leakage_audit import BLOCKED, OK, audit_feature_leakage


def clean_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_id": ["t1", "t2"],
            "symbol": ["BTCUSDT", "ETHUSDT"],
            "open_ts": pd.date_range("2026-01-01", periods=2, freq="min", tz="UTC"),
            "target_win": [1, 0],
            "open_1m_ret": [0.01, -0.02],
            "volume_rel_30": [1.1, 0.9],
        }
    )


def test_detects_future_ret_as_leakage() -> None:
    frame = clean_frame().assign(future_ret_3=[0.1, -0.1])

    report = audit_feature_leakage(frame)

    assert report.status == BLOCKED
    assert any(item.startswith("future_ret_3:") for item in report.forbidden_features)


def test_detects_target_column_between_features_as_leakage() -> None:
    frame = clean_frame()

    report = audit_feature_leakage(
        frame,
        feature_columns=["open_1m_ret", "target_win"],
        target_column="target_win",
    )

    assert report.leakage_detected
    assert "target_win" in report.forbidden_columns


def test_detects_return_pct_as_leakage_when_used_as_feature() -> None:
    frame = clean_frame().assign(return_pct=[0.02, -0.01])

    report = audit_feature_leakage(frame, feature_columns=["open_1m_ret", "return_pct"])

    assert report.status == BLOCKED
    assert "return_pct" in report.forbidden_columns


def test_allows_target_win_as_target_when_excluded_from_features() -> None:
    report = audit_feature_leakage(clean_frame())

    assert report.status == OK
    assert "target_win" not in report.allowed_features
    assert "target_win" in report.dropped_columns


def test_blocks_close_features_when_decision_mode_is_open() -> None:
    frame = clean_frame().assign(close_1m_ret=[0.01, 0.02])

    report = audit_feature_leakage(frame, decision_mode="open")

    assert report.status == BLOCKED
    assert "close_1m_ret" in report.forbidden_columns


def test_allows_open_1m_features_in_open_mode() -> None:
    report = audit_feature_leakage(clean_frame(), decision_mode="open")

    assert report.status == OK
    assert "open_1m_ret" in report.allowed_features


def test_report_is_serializable_and_blocked_when_leakage_exists() -> None:
    frame = clean_frame().assign(pnl=[1.0, -1.0])

    payload = audit_feature_leakage(frame).to_dict()

    assert payload["status"] == BLOCKED
    assert payload["leakage_detected"] is True
    assert payload["total_columns"] == len(frame.columns)


def test_report_is_ok_when_features_are_clean() -> None:
    report = audit_feature_leakage(clean_frame())

    assert report.status == OK
    assert not report.leakage_detected
    assert report.forbidden_features == []
