from __future__ import annotations

import pandas as pd
import pytest

from smartcrypto.research.paper_profit_protection_path_faithful.simulation import (
    simulate_trade_path,
)


def short_trade(*, net_pnl: float = -1.0) -> pd.Series:
    return pd.Series(
        {
            "stable_trade_id": "freqtrade-paper-9001",
            "trade_id": 9001,
            "symbol": "ETHUSDT",
            "side": "short",
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


def short_path(*, gap_second_candle: bool = False) -> pd.DataFrame:
    second = (
        {
            "ts": pd.Timestamp("2026-01-01T00:01:00Z"),
            "open": 99.8,
            "high": 99.9,
            "low": 99.6,
            "close": 99.8,
            "symbol": "ETHUSDT",
            "tf": "1m",
        }
        if gap_second_candle
        else {
            "ts": pd.Timestamp("2026-01-01T00:01:00Z"),
            "open": 99.2,
            "high": 99.4,
            "low": 99.0,
            "close": 99.2,
            "symbol": "ETHUSDT",
            "tf": "1m",
        }
    )
    return pd.DataFrame(
        [
            {
                "ts": pd.Timestamp("2026-01-01T00:00:00Z"),
                "open": 100.0,
                "high": 100.0,
                "low": 99.0,
                "close": 99.1,
                "symbol": "ETHUSDT",
                "tf": "1m",
            },
            second,
            {
                "ts": pd.Timestamp("2026-01-01T00:02:00Z"),
                "open": 99.2,
                "high": 99.3,
                "low": 99.0,
                "close": 99.2,
                "symbol": "ETHUSDT",
                "tf": "1m",
            },
        ]
    )


def test_short_trailing_floor_is_directionally_symmetric() -> None:
    result = simulate_trade_path(
        short_trade(),
        path=short_path(),
        timeframe="1m",
        trigger_mfe_pct=0.001,
        retention_fraction=0.75,
    )

    assert result["stop_hit"] is True
    assert result["gap_through_count"] == 0
    assert result["exit_price"] == pytest.approx(99.25)
    assert result["candidate_net_pnl"] > 0.0


def test_short_gap_above_floor_uses_worse_open_fill() -> None:
    result = simulate_trade_path(
        short_trade(),
        path=short_path(gap_second_candle=True),
        timeframe="1m",
        trigger_mfe_pct=0.001,
        retention_fraction=0.75,
    )

    assert result["stop_hit"] is True
    assert result["gap_through_count"] == 1
    assert result["exit_price"] == pytest.approx(99.8)
