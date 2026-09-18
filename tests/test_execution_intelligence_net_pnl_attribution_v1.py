from __future__ import annotations

import pandas as pd
import pytest

from smartcrypto.research.aibot_parity.execution_intelligence_net_pnl_attribution import (
    ExecutionAttributionError,
    evaluate_execution_net_pnl_attribution,
)


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_sequence": [1, 2, 3, 4],
            "symbol": ["BTC_USDT", "BTC_USDT", "ETH_USDT", "ETH_USDT"],
            "side": ["LONG", "SHORT", "LONG", "SHORT"],
            "horario_abertura": [
                "2026-06-02 00:00:00",
                "2026-06-02 01:00:00",
                "2026-06-02 02:00:00",
                "2026-06-02 03:00:00",
            ],
            "horario_fechamento": [
                "2026-06-02 00:10:00",
                "2026-06-02 01:20:00",
                "2026-06-02 02:40:00",
                "2026-06-02 03:10:00",
            ],
            "reported_pnl": [10.0, 5.0, -4.0, 2.0],
            "economic_fee_adjustment": [-1.0, -1.0, -0.5, -3.0],
            "economic_net_pnl": [9.0, 4.0, -4.5, -1.0],
            "taxa_lucros_perdas_fechados_pct": [1.0, 1.0, -1.0, 1.0],
            "fee_semantics_regime": ["STANDARD"] * 4,
        }
    )


def test_exact_fee_attribution_and_winner_flip() -> None:
    report = evaluate_execution_net_pnl_attribution(
        _frame(),
        enforce_branch08_baseline=False,
    )

    assert report["status"] == "ok"
    common = report["branch08_common_window"]
    assert common["trade_count"] == 4
    assert common["reported_pnl"] == pytest.approx(13.0)
    assert common["economic_fee_adjustment"] == pytest.approx(-5.5)
    assert common["observable_fee_impact_usdt"] == pytest.approx(5.5)
    assert common["economic_net_pnl"] == pytest.approx(7.5)
    assert common["reported_winner_to_net_nonwinner_count"] == 1
    assert report["execution_simulator_invoked"] is False
    assert report["operational_authority"] is False
    assert report["sends_orders"] is False
    assert report["writes_data"] is False


def test_priority_duration_is_diagnostic_only() -> None:
    report = evaluate_execution_net_pnl_attribution(
        _frame(),
        enforce_branch08_baseline=False,
    )

    priority = report["priority_segment"]
    assert priority["dimension"] == "duration_bucket"
    assert priority["bucket"] == "<15m"
    assert priority["trade_count"] == 2
    assert priority["diagnostic_only"] is True
    assert priority["future_duration_used_for_decision"] is False


def test_unobservable_execution_components_are_not_invented() -> None:
    report = evaluate_execution_net_pnl_attribution(
        _frame(),
        enforce_branch08_baseline=False,
    )

    unavailable = report["unobservable_actual_execution_components"]
    assert set(unavailable) == {
        "spread",
        "slippage",
        "market_impact",
        "latency",
        "maker_taker_role",
    }
    assert report["attribution_scope"] == "observable_fee_adjustment_only"
    assert report["residual_execution_effects_may_be_embedded_in_reported_pnl"] is True


def test_economic_identity_drift_fails_closed() -> None:
    frame = _frame()
    frame.loc[0, "economic_net_pnl"] = 999.0

    with pytest.raises(
        ExecutionAttributionError,
        match="economic_identity_mismatch",
    ):
        evaluate_execution_net_pnl_attribution(
            frame,
            enforce_branch08_baseline=False,
        )
