from __future__ import annotations

import pandas as pd

from smartcrypto.research.paper_profit_protection_path_faithful.simulation import (
    simulate_trade_path,
)


def test_floor_does_not_arm_above_running_mfe_after_costs() -> None:
    trade = pd.Series(
        {
            "stable_trade_id": "freqtrade-paper-9901",
            "trade_id": 9901,
            "symbol": "ETHUSDT",
            "side": "long",
            "open_time_utc": pd.Timestamp("2026-01-01T00:00:00Z"),
            "close_time_utc": pd.Timestamp("2026-01-01T00:03:00Z"),
            "entry_price": 100.0,
            "quantity": 1.0,
            "contract_size": 1.0,
            "fees": 0.05,
            "funding": 0.0,
            "net_pnl": -0.5,
        }
    )
    path = pd.DataFrame(
        [
            {
                "ts": pd.Timestamp("2026-01-01T00:00:00Z"),
                "open": 100.0,
                "high": 100.11,
                "low": 100.0,
                "close": 100.08,
                "symbol": "ETHUSDT",
                "tf": "1m",
            },
            {
                "ts": pd.Timestamp("2026-01-01T00:01:00Z"),
                "open": 100.08,
                "high": 100.11,
                "low": 99.50,
                "close": 99.70,
                "symbol": "ETHUSDT",
                "tf": "1m",
            },
            {
                "ts": pd.Timestamp("2026-01-01T00:02:00Z"),
                "open": 99.70,
                "high": 99.80,
                "low": 99.40,
                "close": 99.50,
                "symbol": "ETHUSDT",
                "tf": "1m",
            },
        ]
    )

    result = simulate_trade_path(
        trade,
        path=path,
        timeframe="1m",
        trigger_mfe_pct=0.001,
        retention_fraction=0.0,
    )

    assert result["stop_hit"] is False
    assert result["candidate_net_pnl"] == -0.5
    assert result["unattainable_floor_skip_count"] >= 1
