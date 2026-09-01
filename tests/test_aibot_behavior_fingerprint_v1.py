from __future__ import annotations

import pytest
import pandas as pd

from smartcrypto.research.aibot_parity import (
    SOURCE_INVESTMENT_ID,
    build_behavior_fingerprint,
    build_rolling_behavior,
    canonicalize_trader_master_frame,
)


def _canonical_frame() -> pd.DataFrame:
    raw = pd.DataFrame(
        {
            "order_id": ["1", "2", "3", "4"],
            "moeda": ["BTCUSDT", "BTCUSDT", "ETHUSDT", "ETHUSDT"],
            "fechar_side": ["long", "long", "short", "short"],
            "horario_abertura": [
                "2026-01-01T00:00:00Z",
                "2026-01-08T01:00:00Z",
                "2026-02-08T02:00:00Z",
                "2026-04-08T03:00:00Z",
            ],
            "horario_fechamento": [
                "2026-01-01T00:10:00Z",
                "2026-01-08T01:20:00Z",
                "2026-02-08T02:30:00Z",
                "2026-04-08T04:00:00Z",
            ],
            "pnl_fechado": [10.0, -5.0, 0.0, 5.0],
            "exit_reason": ["roi", "stop_loss", "manual", "roi"],
        }
    )
    return canonicalize_trader_master_frame(
        raw,
        source_investment_id=SOURCE_INVESTMENT_ID,
        source_batch_id="aibot_sha256_" + "1" * 64,
    )


def test_global_behavior_metrics_are_financially_consistent() -> None:
    report = build_behavior_fingerprint(
        _canonical_frame(),
        source_investment_id=SOURCE_INVESTMENT_ID,
        source_batch_id="batch",
    )
    metrics = report["global"]

    assert metrics["trade_count"] == 4
    assert metrics["wins"] == 2
    assert metrics["losses"] == 1
    assert metrics["breakeven"] == 1
    assert metrics["win_rate"] == pytest.approx(0.5)
    assert metrics["gross_profit"] == pytest.approx(15.0)
    assert metrics["gross_loss"] == pytest.approx(5.0)
    assert metrics["net_pnl"] == pytest.approx(10.0)
    assert metrics["profit_factor"] == pytest.approx(3.0)
    assert metrics["expectancy"] == pytest.approx(2.5)
    assert metrics["payoff"] == pytest.approx(1.5)
    assert metrics["max_drawdown"] == pytest.approx(5.0)
    assert metrics["max_winning_streak"] == 1
    assert metrics["max_losing_streak"] == 1
    assert metrics["avg_duration"] == pytest.approx(1800.0)
    assert metrics["median_duration"] == pytest.approx(1500.0)


def test_segmentations_preserve_symbol_side_and_exit_reason() -> None:
    report = build_behavior_fingerprint(
        _canonical_frame(),
        source_investment_id=SOURCE_INVESTMENT_ID,
        source_batch_id="batch",
    )

    assert {row["segment"] for row in report["segmentations"]["symbol"]} == {
        "symbol=BTCUSDT",
        "symbol=ETHUSDT",
    }
    assert {row["segment"] for row in report["segmentations"]["side"]} == {
        "side=long",
        "side=short",
    }
    assert "exit_reason" in report["segmentations"]
    assert report["outcomes_used_for_benchmark_only"] is True
    assert report["training_performed"] is False


def test_rolling_windows_are_deterministic_and_time_bounded() -> None:
    first = build_rolling_behavior(_canonical_frame())
    second = build_rolling_behavior(_canonical_frame())

    assert first == second
    assert set(first["summary"]) == {"7D", "30D", "90D"}
    assert first["summary"]["7D"]["rolling_trade_count"] == 1
    assert first["summary"]["30D"]["rolling_trade_count"] == 1
    assert first["summary"]["90D"]["rolling_trade_count"] == 2
    assert first["summary"]["90D"]["rolling_expectancy"] == pytest.approx(2.5)
