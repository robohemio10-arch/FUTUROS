from __future__ import annotations

import pandas as pd

from smartcrypto.research.paper_profit_maximization.metrics import (
    normalize_trader_master_rows,
    prepare_profit_dataset,
)


def test_pd_na_rejection_reason_does_not_raise_and_falls_back() -> None:
    frame = pd.DataFrame(
        [
            {
                "stable_trade_id": "freqtrade-paper-9001",
                "analysis_eligible": False,
                "rejection_reason": pd.NA,
                "analysis_block_reason": "upstream_quality_block",
                "net_pnl": -1.0,
            }
        ]
    )

    prepared, _ = prepare_profit_dataset(frame)

    assert prepared.loc[0, "profit_optimization_eligible"] == False  # noqa: E712
    assert (
        prepared.loc[0, "profit_optimization_exclusion_reason"]
        == "upstream_quality_block"
    )


def test_all_missing_upstream_reasons_use_deterministic_default() -> None:
    frame = pd.DataFrame(
        [
            {
                "stable_trade_id": "freqtrade-paper-9002",
                "analysis_eligible": False,
                "rejection_reason": pd.NA,
                "analysis_block_reason": pd.NA,
                "net_pnl": -1.0,
            }
        ]
    )

    prepared, _ = prepare_profit_dataset(frame)

    assert (
        prepared.loc[0, "profit_optimization_exclusion_reason"]
        == "upstream_analysis_ineligible"
    )


def test_trader_master_scalar_pd_na_fields_normalize_without_boolean_coercion() -> None:
    rows = [
        {
            "order_id": "master-1",
            "symbol": pd.NA,
            "moeda": "ETHUSDT",
            "side": pd.NA,
            "fechar_side": "short",
            "open_time": pd.NA,
            "horario_abertura": "2026-08-01T00:00:00Z",
            "close_time": pd.NA,
            "horario_fechamento": "2026-08-01T00:05:00Z",
            "pnl_fechado": 1.25,
        }
    ]

    normalized = normalize_trader_master_rows(rows)

    assert len(normalized) == 1
    assert normalized.loc[0, "symbol"] == "ETHUSDT"
    assert normalized.loc[0, "side"] == "short"
    assert normalized.loc[0, "open_time_utc"] == pd.Timestamp("2026-08-01T00:00:00Z")
    assert normalized.loc[0, "close_time_utc"] == pd.Timestamp("2026-08-01T00:05:00Z")
