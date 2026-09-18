from __future__ import annotations

import pandas as pd

from smartcrypto.research.aibot_parity.opportunity_allocator_capital_hour_uplift import (
    TOP_N_OPPORTUNITIES,
    evaluate_opportunity_allocator,
)


PERIODS = (
    "2026-01",
    "2026-02",
    "2026-03",
    "2026-04",
    "2026-05",
    "2026-06",
    "2026-07",
    "2026-08",
)


def _frame(*, invert_future: bool = False) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    sequence = 1
    keys = (
        ("BTC_USDT", "LONG", 4.0),
        ("ETH_USDT", "LONG", 2.0),
        ("BTC_USDT", "SHORT", -1.0),
        ("ETH_USDT", "SHORT", -2.0),
    )

    for period_index, period in enumerate(PERIODS):
        start = pd.Timestamp(f"{period}-01T00:00:00Z")
        for key_index, (symbol, side, base_pnl) in enumerate(keys):
            for offset in range(50):
                opened = start + pd.Timedelta(
                    minutes=(key_index * 60 * 60) + offset * 10
                )
                pnl = base_pnl
                if invert_future and period_index >= 2:
                    pnl = -base_pnl
                rows.append(
                    {
                        "trade_sequence": sequence,
                        "symbol": symbol,
                        "side": side,
                        "horario_abertura": opened.strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                        "horario_fechamento": (
                            opened + pd.Timedelta(hours=1)
                        ).strftime("%Y-%m-%d %H:%M:%S"),
                        "reported_pnl": pnl,
                        "taxa_lucros_perdas_fechados_pct": (
                            1.0 if pnl >= 0.0 else -1.0
                        ),
                        "economic_net_pnl": pnl,
                        "fee_semantics_regime": "STANDARD",
                    }
                )
                sequence += 1

    return pd.DataFrame(rows)


def test_allocator_uses_only_entry_known_symbol_and_side() -> None:
    report = evaluate_opportunity_allocator(_frame())

    assert report["status"] == "ok"
    assert report["selection_dimensions"] == ["symbol", "side"]
    assert report["selection_uses_current_trade_realized_duration"] is False
    assert report["historical_realized_duration_used_in_train_score"] is True
    assert report["selection_uses_current_trade_outcome_fields"] is False
    assert report["historical_outcomes_used_in_train_score"] is True
    assert report["selection_uses_test_period_metrics"] is False
    assert report["priority_segment_duration_lt_15m"]["used_for_selection"] is False
    assert report["sends_orders"] is False
    assert report["writes_data"] is False


def test_expanding_allocator_selects_top_two_historical_efficiency_keys() -> None:
    report = evaluate_opportunity_allocator(_frame())

    assert report["fold_count"] == 6
    assert report["top_n_opportunities"] == TOP_N_OPPORTUNITIES == 2
    for fold in report["folds"]:
        assert fold["selected_opportunity_keys"] == [
            "BTCUSDT|long",
            "ETHUSDT|long",
        ]
        assert fold["score_source"] == "purged_train_rows_only"
        assert fold["treatment"]["trade_count"] == 100
        assert fold["control"]["trade_count"] == 200


def test_future_outcomes_do_not_change_same_fold_selection_keys() -> None:
    original = evaluate_opportunity_allocator(_frame())
    inverted = evaluate_opportunity_allocator(_frame(invert_future=True))

    original_keys = [
        fold["selected_opportunity_keys"]
        for fold in original["folds"]
    ]
    inverted_keys = [
        fold["selected_opportunity_keys"]
        for fold in inverted["folds"]
    ]

    assert original_keys[0] == inverted_keys[0]
    assert original_keys[1] != []
    assert inverted_keys[1] != []


def test_capital_hour_objective_produces_positive_uplift_in_fixture() -> None:
    report = evaluate_opportunity_allocator(_frame())

    combined = report["combined_oos"]
    assert combined["control"]["trade_count"] == 1200
    assert combined["treatment"]["trade_count"] == 600
    assert (
        combined["treatment"]["net_pnl_per_capital_hour"]
        > combined["control"]["net_pnl_per_capital_hour"]
    )
    assert combined["net_pnl_per_capital_hour_uplift"] > 0.0
    assert report["decision"] == "CAPITAL_HOUR_UPLIFT_OBSERVED_RESEARCH_ONLY"
