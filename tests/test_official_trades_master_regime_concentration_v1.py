from __future__ import annotations

import math

import numpy as np
import pandas as pd

from smartcrypto.research.trades_master_official.regime_concentration import (
    EXPECTED_PRIMARY_OOS_ROWS,
    METHOD_FREEZE_V2_FILE_SHA256,
    WQ4_METHOD_HASH,
    _classify_wq4,
    _metrics,
    _profit_factor_gt_one,
    _rolling_current_percentile_rank,
    report_content_sha256,
)


def test_frozen_wq4_constants() -> None:
    assert WQ4_METHOD_HASH == ("e837eda4f95adbf4bc725ac7113b56a7562917eb912cc98442b2cbebf96dd6a0")
    assert METHOD_FREEZE_V2_FILE_SHA256 == (
        "62bf92f8eb66f35e43844bb81a9430099d30e121ca89c22b189646a4d3aaf0ec"
    )
    assert EXPECTED_PRIMARY_OOS_ROWS == 3066


def test_rolling_percentile_rank_requires_720_values() -> None:
    timestamps = pd.Series(
        pd.date_range(
            "2026-01-01T00:00:00Z",
            periods=720,
            freq="1min",
        )
    )
    values = pd.Series(np.ones(720, dtype=float))

    result = _rolling_current_percentile_rank(
        values,
        timestamps,
    )

    assert np.isnan(result[718])
    assert result[719] == 0.5


def test_rolling_percentile_rank_uses_midrank() -> None:
    timestamps = pd.Series(
        pd.date_range(
            "2026-01-01T00:00:00Z",
            periods=720,
            freq="1min",
        )
    )
    values = pd.Series(np.arange(720, dtype=float))

    result = _rolling_current_percentile_rank(
        values,
        timestamps,
    )

    expected = 719.5 / 720.0
    assert math.isclose(
        result[-1],
        expected,
        rel_tol=0.0,
        abs_tol=1e-15,
    )


def test_rolling_percentile_rank_resets_on_gap() -> None:
    first = pd.date_range(
        "2026-01-01T00:00:00Z",
        periods=719,
        freq="1min",
    )
    second = pd.DatetimeIndex([first[-1] + pd.Timedelta(minutes=2)])
    timestamps = pd.Series(first.append(second))
    values = pd.Series(np.ones(720, dtype=float))

    result = _rolling_current_percentile_rank(
        values,
        timestamps,
    )

    assert np.isnan(result[-1])


def test_metrics_known_sequence() -> None:
    frame = pd.DataFrame(
        {
            "economic_net_pnl": [
                10.0,
                -4.0,
                -3.0,
                8.0,
            ]
        }
    )

    metrics = _metrics(
        frame,
        baseline_net_pnl=11.0,
    )

    assert metrics["trade_count"] == 4
    assert metrics["net_pnl"] == 11.0
    assert metrics["gross_profit"] == 18.0
    assert metrics["gross_loss"] == -7.0
    assert math.isclose(
        metrics["profit_factor"],
        18.0 / 7.0,
    )
    assert metrics["win_rate"] == 0.5
    assert metrics["expectancy"] == 2.75
    assert metrics["max_drawdown"] == 7.0
    assert metrics["net_pnl_share"] == 1.0


def test_profit_factor_without_losses_is_positive_infinite_semantics() -> None:
    metrics = {
        "gross_profit": 10.0,
        "gross_loss": 0.0,
        "profit_factor": None,
    }

    assert _profit_factor_gt_one(metrics) is True


def test_wq4_classification_order() -> None:
    assert (
        _classify_wq4(
            august_pass=False,
            trimmed_pass=True,
            positive_supported_regime_count=9,
        )
        == "FRAGILE"
    )
    assert (
        _classify_wq4(
            august_pass=True,
            trimmed_pass=False,
            positive_supported_regime_count=9,
        )
        == "FRAGILE"
    )
    assert (
        _classify_wq4(
            august_pass=True,
            trimmed_pass=True,
            positive_supported_regime_count=1,
        )
        == "CONCENTRATED_RESEARCH_CANDIDATE"
    )
    assert (
        _classify_wq4(
            august_pass=True,
            trimmed_pass=True,
            positive_supported_regime_count=2,
        )
        == "REGIME_ROBUST_RESEARCH_CANDIDATE"
    )


def test_report_hash_ignores_derived_transport_fields() -> None:
    base = {
        "schema_version": "x",
        "status": "ok",
        "value": 123,
    }

    first = report_content_sha256(base)

    enriched = {
        **base,
        "report_content_sha256": "ignored",
        "report_paths": {"json": "ignored"},
    }

    second = report_content_sha256(enriched)

    assert first == second
