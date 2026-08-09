from __future__ import annotations

import pandas as pd
import pytest

from smartcrypto.research.paper_profit_momentum_protection_ab.contracts import (
    RET1_THRESHOLD,
    RET12_THRESHOLD,
    SAFETY_FLAGS,
)
from smartcrypto.research.paper_profit_momentum_protection_ab.simulation import (
    ARM_CONTROL,
    ARM_RET12,
    ARM_RET12_RET1,
    _simulate_protection,
    build_momentum_arm_masks,
    evaluate_momentum_protection_ab,
    rank_ab_candidates,
)


def paper_frame(count: int = 60) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    for index in range(count):
        strong = index % 3 == 0
        winner = strong and index % 9 != 0
        if strong:
            net_pnl = 1.2 if winner else -0.9
            mfe_absolute = 1.8 if winner else 1.4
            mfe_pct = 0.008
            retracement = 0.25 if winner else 1.4
            ret12 = 0.006
            ret1 = 0.002
        else:
            net_pnl = -0.8
            mfe_absolute = 0.4
            mfe_pct = 0.002
            retracement = 0.4
            ret12 = -0.002
            ret1 = -0.001
        rows.append(
            {
                "stable_trade_id": f"freqtrade-paper-{1000 + index}",
                "trade_id": 1000 + index,
                "symbol": "ETHUSDT" if index % 2 else "BTCUSDT",
                "side": "short" if index % 2 else "long",
                "open_time_utc": start + pd.Timedelta(minutes=index * 10),
                "close_time_utc": start + pd.Timedelta(minutes=index * 10 + 5),
                "net_pnl": net_pnl,
                "analysis_eligible": True,
                "financial_decomposition_status": "authoritative_reconciled",
                "accounting_reconciled": True,
                "rejection_reason": pd.NA,
                "analysis_block_reason": pd.NA,
                "entry_return_12": ret12,
                "entry_return_1": ret1,
                "mfe_absolute": mfe_absolute,
                "mfe_pct": mfe_pct,
                "mae_absolute": -1.0,
                "mae_pct": -0.004,
                "time_to_mfe_seconds": 60.0,
                "time_to_mae_seconds": 240.0,
                "retracement_after_mfe_absolute": retracement,
                "winner_to_loser_conversion": net_pnl < 0 and mfe_absolute > 0,
                "fees": 0.10,
                "funding": 0.0,
            }
        )
    return pd.DataFrame(rows)


def test_authorized_momentum_arm_thresholds_are_exact() -> None:
    frame = paper_frame(12)
    frame.loc[0, "entry_return_12"] = RET12_THRESHOLD
    frame.loc[0, "entry_return_1"] = RET1_THRESHOLD
    frame.loc[1, "entry_return_12"] = RET12_THRESHOLD - 1e-12
    frame.loc[1, "entry_return_1"] = RET1_THRESHOLD

    masks = build_momentum_arm_masks(frame)

    assert masks[ARM_CONTROL].all()
    assert bool(masks[ARM_RET12].iloc[0]) is True
    assert bool(masks[ARM_RET12_RET1].iloc[0]) is True
    assert bool(masks[ARM_RET12].iloc[1]) is False
    assert bool(masks[ARM_RET12_RET1].iloc[1]) is False


def test_momentum_ab_reports_control_ret12_and_combined_arms() -> None:
    _, report = evaluate_momentum_protection_ab(paper_frame())

    arms = {item["arm_id"]: item for item in report["arm_results"]}
    assert set(arms) == {ARM_CONTROL, ARM_RET12, ARM_RET12_RET1}
    assert arms[ARM_CONTROL]["selected_trade_count"] == 60
    assert arms[ARM_RET12]["selected_trade_count"] == 20
    assert arms[ARM_RET12_RET1]["selected_trade_count"] == 20
    assert arms[ARM_RET12]["metrics"]["net_pnl"] > 0
    assert arms[ARM_RET12_RET1]["oos_metrics"]["net_pnl"] > 0


def test_net_breakeven_protection_is_cost_aware() -> None:
    frame = pd.DataFrame(
        [
            {
                "net_pnl": -1.0,
                "mfe_absolute": 1.0,
                "mfe_pct": 0.01,
                "retracement_after_mfe_absolute": 1.0,
                "fees": 0.10,
                "funding": 0.0,
                "winner_to_loser_conversion": True,
            }
        ]
    )

    result = _simulate_protection(
        frame,
        trigger_pct=0.005,
        retention_fraction=0.0,
    )

    assert result["pessimistic_frame"].iloc[0]["net_pnl"] == pytest.approx(0.0)
    assert result["optimistic_frame"].iloc[0]["net_pnl"] == pytest.approx(0.0)
    assert result["diagnostics"]["pessimistic_saved_loser_count"] == 1


def test_retained_mfe_floor_can_turn_loser_positive_after_costs() -> None:
    frame = pd.DataFrame(
        [
            {
                "net_pnl": -1.0,
                "mfe_absolute": 2.0,
                "mfe_pct": 0.01,
                "retracement_after_mfe_absolute": 2.0,
                "fees": 0.10,
                "funding": 0.0,
                "winner_to_loser_conversion": True,
            }
        ]
    )

    result = _simulate_protection(
        frame,
        trigger_pct=0.005,
        retention_fraction=0.50,
    )

    assert result["pessimistic_frame"].iloc[0]["net_pnl"] == pytest.approx(0.90)
    assert result["diagnostics"]["pessimistic_recovered_winner_to_loser_count"] == 1
    assert result["diagnostics"]["pessimistic_recovered_winner_to_loser_pnl"] == pytest.approx(
        1.90
    )


def test_pessimistic_bound_counts_winner_sacrifice() -> None:
    frame = pd.DataFrame(
        [
            {
                "net_pnl": 1.5,
                "mfe_absolute": 2.0,
                "mfe_pct": 0.01,
                "retracement_after_mfe_absolute": 1.5,
                "fees": 0.10,
                "funding": 0.0,
                "winner_to_loser_conversion": False,
            }
        ]
    )

    result = _simulate_protection(
        frame,
        trigger_pct=0.005,
        retention_fraction=0.50,
    )

    assert result["optimistic_frame"].iloc[0]["net_pnl"] == pytest.approx(1.5)
    assert result["pessimistic_frame"].iloc[0]["net_pnl"] == pytest.approx(0.90)
    assert result["diagnostics"]["pessimistic_harmed_winner_count"] == 1
    assert result["diagnostics"]["pessimistic_winner_pnl_sacrificed"] == pytest.approx(
        0.60
    )


def test_missing_path_or_cost_data_is_not_silently_simulated() -> None:
    frame = pd.DataFrame(
        [
            {
                "net_pnl": -1.0,
                "mfe_absolute": 2.0,
                "mfe_pct": 0.01,
                "retracement_after_mfe_absolute": pd.NA,
                "fees": 0.10,
                "funding": 0.0,
                "winner_to_loser_conversion": True,
            }
        ]
    )

    result = _simulate_protection(
        frame,
        trigger_pct=0.005,
        retention_fraction=0.50,
    )

    assert result["pessimistic_frame"].iloc[0]["net_pnl"] == pytest.approx(-1.0)
    assert result["diagnostics"]["simulation_incomplete_trade_count"] == 1
    assert result["diagnostics"]["protection_armed_trade_count"] == 0


def test_full_ab_is_read_only_and_preserves_source_frame() -> None:
    frame = paper_frame()
    original = frame.copy(deep=True)

    dataset, report = evaluate_momentum_protection_ab(frame)

    pd.testing.assert_frame_equal(frame, original)
    assert report["status"] == "ok"
    assert report["protection_candidate_count"] == 60
    assert "ab_arm_ret12_eligible" in dataset.columns
    assert "ab_arm_ret12_ret1_eligible" in dataset.columns


def test_best_candidate_must_be_positive_on_robust_oos_bound() -> None:
    _, report = evaluate_momentum_protection_ab(paper_frame())

    best = report["best_candidate"]
    assert best is not None
    assert best["decision"] == "PROMOVER_PARA_PAPER_AB"
    assert best["robust_net_pnl"] > 0
    assert best["robust_oos_net_pnl"] > 0
    assert best["robust_expectancy"] > 0
    assert best["robust_oos_expectancy"] > 0
    assert best["robust_profit_factor"] > 1.0
    assert best["robust_oos_profit_factor"] > 1.0


def test_rank_prefers_robust_oos_improvement_over_in_sample_gain() -> None:
    candidates = [
        {
            "candidate_id": "large_train",
            "decision": "PROMOVER_PARA_PAPER_AB",
            "robust_oos_delta_vs_global_baseline": 2.0,
            "robust_oos_net_pnl": 3.0,
            "robust_net_pnl": 100.0,
            "robust_maximum_drawdown": 2.0,
            "pessimistic_harmed_winner_count": 0,
        },
        {
            "candidate_id": "strong_oos",
            "decision": "PROMOVER_PARA_PAPER_AB",
            "robust_oos_delta_vs_global_baseline": 5.0,
            "robust_oos_net_pnl": 6.0,
            "robust_net_pnl": 10.0,
            "robust_maximum_drawdown": 3.0,
            "pessimistic_harmed_winner_count": 1,
        },
    ]

    ranked = rank_ab_candidates(candidates)

    assert ranked[0]["candidate_id"] == "strong_oos"


def test_safety_flags_forbid_operational_mutation() -> None:
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
    assert SAFETY_FLAGS["updates_freqtrade"] is False
    assert SAFETY_FLAGS["deploy_performed"] is False
