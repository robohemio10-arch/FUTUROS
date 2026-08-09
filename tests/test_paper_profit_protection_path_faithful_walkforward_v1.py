from __future__ import annotations

import pandas as pd
import pytest

from smartcrypto.research.paper_profit_protection_path_faithful.contracts import (
    EXIT_SLIPPAGE_BPS,
    FIXED_PROTECTION_CANDIDATES,
    SAFETY_FLAGS,
)
from smartcrypto.research.paper_profit_protection_path_faithful.simulation import (
    build_walkforward_folds,
    rank_development_candidates,
    simulate_trade_path,
    validate_path_faithful_candidates,
)


def trade_row(*, trade_id: int = 1000, net_pnl: float = -1.0) -> pd.Series:
    return pd.Series(
        {
            "stable_trade_id": f"freqtrade-paper-{trade_id}",
            "trade_id": trade_id,
            "symbol": "ETHUSDT",
            "side": "long",
            "open_time_utc": pd.Timestamp("2026-01-01T00:00:00Z"),
            "close_time_utc": pd.Timestamp("2026-01-01T00:03:00Z"),
            "entry_price": 100.0,
            "quantity": 1.0,
            "contract_size": 1.0,
            "fees": 0.0,
            "funding": 0.0,
            "net_pnl": net_pnl,
        }
    )


def candle_path(rows: list[tuple[str, float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ts": pd.Timestamp(timestamp),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "symbol": "ETHUSDT",
                "tf": "1m",
            }
            for timestamp, open_price, high, low, close in rows
        ]
    )


def test_fixed_candidate_set_is_exactly_the_four_preauthorized_policies() -> None:
    assert [item["candidate_id"] for item in FIXED_PROTECTION_CANDIDATES] == [
        "trigger_10bps__retain_75pct_mfe",
        "trigger_10bps__net_breakeven",
        "trigger_10bps__retain_50pct_mfe",
        "trigger_25bps__retain_75pct_mfe",
    ]


def test_new_peak_only_arms_trailing_for_subsequent_candle() -> None:
    trade = trade_row(net_pnl=-0.5)
    path = candle_path(
        [
            ("2026-01-01T00:00:00Z", 100.0, 101.0, 99.0, 100.2),
            ("2026-01-01T00:01:00Z", 100.9, 100.9, 100.1, 100.3),
            ("2026-01-01T00:02:00Z", 100.3, 100.4, 100.2, 100.3),
        ]
    )

    result = simulate_trade_path(
        trade,
        path=path,
        timeframe="1m",
        trigger_mfe_pct=0.001,
        retention_fraction=0.75,
    )

    assert result["stop_hit"] is True
    assert result["gap_through_count"] == 0
    assert result["intrabar_ambiguous_count"] == 0
    assert result["exit_price"] == pytest.approx(100.75)


def test_single_ambiguous_candle_does_not_use_its_future_high_to_arm_stop() -> None:
    trade = trade_row(net_pnl=-0.5)
    trade["close_time_utc"] = pd.Timestamp("2026-01-01T00:01:00Z")
    path = candle_path(
        [("2026-01-01T00:00:00Z", 100.0, 101.0, 99.0, 100.2)]
    )

    result = simulate_trade_path(
        trade,
        path=path,
        timeframe="1m",
        trigger_mfe_pct=0.001,
        retention_fraction=0.75,
    )

    assert result["stop_hit"] is False
    assert result["candidate_net_pnl"] == pytest.approx(-0.5)


def test_gap_through_floor_uses_worse_candle_open_fill() -> None:
    trade = trade_row(net_pnl=-1.0)
    path = candle_path(
        [
            ("2026-01-01T00:00:00Z", 100.0, 101.0, 100.0, 100.9),
            ("2026-01-01T00:01:00Z", 100.2, 100.4, 99.9, 100.1),
            ("2026-01-01T00:02:00Z", 100.1, 100.2, 100.0, 100.1),
        ]
    )

    result = simulate_trade_path(
        trade,
        path=path,
        timeframe="1m",
        trigger_mfe_pct=0.001,
        retention_fraction=0.75,
    )

    assert result["stop_hit"] is True
    assert result["gap_through_count"] == 1
    assert result["exit_price"] == pytest.approx(100.2)
    assert result["candidate_net_pnl"] < 0.2


def test_partial_entry_and_exit_candles_are_excluded() -> None:
    trade = trade_row(net_pnl=-0.7)
    trade["open_time_utc"] = pd.Timestamp("2026-01-01T00:00:30Z")
    trade["close_time_utc"] = pd.Timestamp("2026-01-01T00:03:30Z")
    path = candle_path(
        [
            ("2026-01-01T00:00:00Z", 100.0, 105.0, 95.0, 100.0),
            ("2026-01-01T00:01:00Z", 100.0, 100.05, 99.95, 100.0),
            ("2026-01-01T00:02:00Z", 100.0, 100.05, 99.95, 100.0),
            ("2026-01-01T00:03:00Z", 100.1, 106.0, 94.0, 100.0),
        ]
    )

    result = simulate_trade_path(
        trade,
        path=path,
        timeframe="1m",
        trigger_mfe_pct=0.001,
        retention_fraction=0.75,
    )

    assert result["boundary_candles_excluded"] == 2
    assert result["stop_hit"] is False
    assert result["candidate_net_pnl"] == pytest.approx(-0.7)


def test_fixed_exit_slippage_is_charged_and_not_optimized() -> None:
    assert EXIT_SLIPPAGE_BPS == 10.0
    trade = trade_row(net_pnl=-1.0)
    path = candle_path(
        [
            ("2026-01-01T00:00:00Z", 100.0, 101.0, 100.0, 100.9),
            ("2026-01-01T00:01:00Z", 100.9, 101.0, 100.0, 100.8),
            ("2026-01-01T00:02:00Z", 100.8, 100.9, 100.0, 100.8),
        ]
    )

    result = simulate_trade_path(
        trade,
        path=path,
        timeframe="1m",
        trigger_mfe_pct=0.001,
        retention_fraction=0.0,
    )

    assert result["stop_hit"] is True
    assert abs(float(result["candidate_net_pnl"])) <= 0.002


def test_walkforward_builds_three_expanding_history_folds() -> None:
    folds = build_walkforward_folds(80)

    assert len(folds) == 3
    assert folds[0]["train_start"] == 0
    assert folds[0]["train_end_exclusive"] == folds[0]["validation_start"]
    assert folds[1]["train_end_exclusive"] > folds[0]["train_end_exclusive"]
    assert folds[2]["validation_end_exclusive"] == 80


def test_ranker_ignores_arbitrary_holdout_fields() -> None:
    base = {
        "development_decision": "FREEZE_FOR_HOLDOUT",
        "positive_walkforward_fold_count": 3,
        "walkforward_total_delta_pnl": 10.0,
        "walkforward_candidate_net_pnl": 8.0,
        "walkforward_profit_factor": 2.0,
        "walkforward_maximum_drawdown": 2.0,
    }
    candidates = [
        {"candidate_id": "a", **base, "holdout_net_pnl": -1000.0},
        {
            "candidate_id": "b",
            **base,
            "walkforward_total_delta_pnl": 9.0,
            "holdout_net_pnl": 1000.0,
        },
    ]

    ranked = rank_development_candidates(candidates)

    assert ranked[0]["candidate_id"] == "a"


def test_end_to_end_freezes_before_holdout_and_can_pass_causal_holdout() -> None:
    rows: list[dict[str, object]] = []
    paths: dict[str, pd.DataFrame] = {}
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    for index in range(100):
        open_time = start + pd.Timedelta(minutes=index * 5)
        close_time = open_time + pd.Timedelta(minutes=3)
        stable_id = f"freqtrade-paper-{2000 + index}"
        rows.append(
            {
                "stable_trade_id": stable_id,
                "trade_id": 2000 + index,
                "symbol": "ETHUSDT",
                "side": "long",
                "open_time_utc": open_time,
                "close_time_utc": close_time,
                "entry_price": 100.0,
                "quantity": 1.0,
                "contract_size": 1.0,
                "fees": 0.0,
                "funding": 0.0,
                "net_pnl": -1.0,
                "analysis_eligible": True,
                "financial_decomposition_status": "authoritative_reconciled",
                "accounting_reconciled": True,
                "rejection_reason": pd.NA,
                "analysis_block_reason": pd.NA,
                "mfe_absolute": 1.0,
                "mfe_pct": 0.01,
                "mae_absolute": -1.0,
                "mae_pct": -0.01,
                "time_to_mfe_seconds": 60.0,
                "time_to_mae_seconds": 120.0,
            }
        )
        paths[stable_id] = candle_path(
            [
                (open_time.isoformat(), 100.0, 101.0, 100.0, 100.9),
                (
                    (open_time + pd.Timedelta(minutes=1)).isoformat(),
                    100.9,
                    101.0,
                    100.6,
                    100.8,
                ),
                (
                    (open_time + pd.Timedelta(minutes=2)).isoformat(),
                    100.8,
                    100.9,
                    100.7,
                    100.8,
                ),
            ]
        )

    dataset, report = validate_path_faithful_candidates(
        pd.DataFrame(rows),
        paths_by_trade=paths,
        timeframe="1m",
    )

    assert report["status"] == "ok"
    assert report["holdout_trade_count"] == 20
    assert report["frozen_champion"] is not None
    assert report["frozen_champion"]["holdout_metrics_used_for_selection"] is False
    assert report["holdout_evaluation"]["holdout_passed"] is True
    assert report["path_faithful_validation_passed"] is True
    assert report["ready_for_paper_wiring"] is True
    assert set(dataset["path_faithful_partition"]) >= {"development", "holdout"}


def test_safety_flags_forbid_all_operational_changes() -> None:
    assert SAFETY_FLAGS["research_only"] is True
    assert SAFETY_FLAGS["read_only"] is True
    assert SAFETY_FLAGS["paper_only"] is True
    assert SAFETY_FLAGS["operational_authority"] is False
    assert SAFETY_FLAGS["sends_orders"] is False
    assert SAFETY_FLAGS["exchange_private_access"] is False
    assert SAFETY_FLAGS["changes_risk"] is False
    assert SAFETY_FLAGS["changes_roi"] is False
    assert SAFETY_FLAGS["changes_stoploss"] is False
    assert SAFETY_FLAGS["writes_runtime"] is False
    assert SAFETY_FLAGS["deploy_performed"] is False
