from __future__ import annotations

import math

import pandas as pd
import pytest

from smartcrypto.research.paper_profit_maximization.candidates import (
    build_combined_filter_candidates,
    build_score_threshold_candidates,
    evaluate_keep_candidate,
)
from smartcrypto.research.paper_profit_maximization.contracts import (
    KNOWN_CORRUPT_PAPER_TRADE_IDS,
    SAFETY_FLAGS,
)
from smartcrypto.research.paper_profit_maximization.metrics import (
    normalize_score_rows,
    prepare_profit_dataset,
    profit_metrics,
)
from smartcrypto.research.paper_profit_maximization.optimizer import (
    build_profit_maximization,
)


def paper_frame(count: int = 40) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    for index in range(count):
        loser = index % 4 == 0
        net_pnl = -2.0 if loser else 1.5
        mfe = 0.8 if loser else 3.0
        rows.append(
            {
                "stable_trade_id": f"freqtrade-paper-{1000 + index}",
                "trade_id": 1000 + index,
                "symbol": "ETHUSDT" if index % 2 else "BTCUSDT",
                "side": "short" if index % 3 else "long",
                "open_time_utc": start + pd.Timedelta(minutes=index * 10),
                "close_time_utc": start + pd.Timedelta(minutes=index * 10 + 5),
                "net_pnl": net_pnl,
                "analysis_eligible": True,
                "financial_decomposition_status": "authoritative_reconciled",
                "mfe_absolute": mfe,
                "mfe_pct": mfe / 100.0,
                "mae_absolute": -2.5 if loser else -0.5,
                "mae_pct": -0.025 if loser else -0.005,
                "time_to_mfe_seconds": 60.0 if not loser else 120.0,
                "time_to_mae_seconds": 60.0 if loser else 240.0,
                "entry_return_6": 0.02 if not loser else -0.02,
                "entry_momentum_6": 0.02 if not loser else -0.02,
                "entry_trend_regime": "uptrend" if not loser else "downtrend",
                "entry_volatility_regime": "normal",
                "entry_candle_direction": "bullish" if not loser else "bearish",
                "entry_hour_utc": index % 24,
                "entry_day_of_week": "Thursday",
            }
        )
    return pd.DataFrame(rows)


def score_rows(frame: pd.DataFrame) -> list[dict[str, object]]:
    rows = []
    for row in frame.to_dict(orient="records"):
        winner = float(row["net_pnl"]) > 0
        rows.append(
            {
                "order_id": row["stable_trade_id"],
                "qlib_score": 0.9 if winner else 0.1,
                "ai_shadow_probability": 0.85 if winner else 0.15,
            }
        )
    return rows


def test_known_corrupt_paper_trades_are_excluded_without_mutating_source() -> None:
    frame = paper_frame(12)
    corrupt = frame.iloc[0].copy()
    corrupt["stable_trade_id"] = "freqtrade-paper-653"
    corrupt["trade_id"] = 653
    corrupt["net_pnl"] = 99.0
    source = pd.concat([frame, corrupt.to_frame().T], ignore_index=True)
    original = source.copy(deep=True)

    prepared, _ = prepare_profit_dataset(source)

    row = prepared.loc[prepared["trade_id_numeric"].eq(653)].iloc[0]
    assert row["profit_optimization_eligible"] == False  # noqa: E712
    assert (
        row["profit_optimization_exclusion_reason"]
        == "known_duplicate_full_exit_financial_corruption"
    )
    pd.testing.assert_frame_equal(source, original)
    assert 653 in KNOWN_CORRUPT_PAPER_TRADE_IDS


def test_accounting_unreconciled_trade_is_excluded() -> None:
    frame = paper_frame(10)
    frame.loc[3, "financial_decomposition_status"] = "accounting_unreconciled"

    prepared, _ = prepare_profit_dataset(frame)

    assert prepared.loc[3, "profit_optimization_eligible"] == False  # noqa: E712
    assert (
        prepared.loc[3, "profit_optimization_exclusion_reason"]
        == "financial_accounting_not_reconciled"
    )


def test_winner_capture_ratio_and_profit_left_on_table_are_computed() -> None:
    frame = paper_frame(10)
    frame.loc[1, "net_pnl"] = 1.5
    frame.loc[1, "mfe_absolute"] = 3.0

    prepared, _ = prepare_profit_dataset(frame)

    assert prepared.loc[1, "winner_capture_ratio"] == pytest.approx(0.5)
    assert prepared.loc[1, "winner_profit_left_on_table"] == pytest.approx(1.5)
    assert prepared.loc[1, "winner_giveback_ratio"] == pytest.approx(0.5)


def test_loser_with_positive_mfe_is_classified_as_winner_to_loser() -> None:
    frame = paper_frame(10)

    prepared, _ = prepare_profit_dataset(frame)

    loser = prepared.loc[prepared["net_pnl"].lt(0)].iloc[0]
    assert loser["loser_type"] == "winner_to_loser"


def test_score_normalization_strips_outcome_prefix_and_detects_conflict() -> None:
    rows = [
        {
            "event_id": "outcome_order_id_freqtrade-paper-77",
            "qlib_score": 0.7,
            "ai_shadow_probability": 0.8,
        },
        {
            "order_id": "freqtrade-paper-77",
            "qlib_score": 0.9,
            "ai_shadow_probability": 0.8,
        },
    ]

    normalized, report = normalize_score_rows(rows)

    assert "qlib_score" not in normalized["freqtrade-paper-77"]
    assert normalized["freqtrade-paper-77"]["ai_shadow_score"] == pytest.approx(0.8)
    assert report["score_conflict_count"] == 1


def test_score_enrichment_builds_rank_and_ensemble_scores() -> None:
    frame = paper_frame(20)

    prepared, report = prepare_profit_dataset(frame, score_rows=score_rows(frame))

    assert report["paper_rows_with_qlib_score"] == 20
    assert report["paper_rows_with_ai_shadow_score"] == 20
    assert report["paper_rows_with_ensemble_score"] == 20
    winners = prepared.loc[prepared["net_pnl"].gt(0)]
    losers = prepared.loc[prepared["net_pnl"].lt(0)]
    assert winners["ensemble_score"].mean() > losers["ensemble_score"].mean()


def test_profit_metrics_prioritize_pnl_expectancy_pf_and_loss_magnitude() -> None:
    frame = paper_frame(20)
    prepared, _ = prepare_profit_dataset(frame)

    metrics = profit_metrics(prepared)

    assert metrics["net_pnl"] > 0
    assert metrics["expectancy"] > 0
    assert metrics["profit_factor"] is not None
    assert metrics["average_win"] > 0
    assert metrics["average_loss"] < 0


def test_entry_filter_can_remove_persistent_negative_slice_and_improve_oos() -> None:
    frame = paper_frame(40)
    prepared, _ = prepare_profit_dataset(frame)
    ordered = prepared.sort_values(["close_time_utc", "stable_trade_id"]).reset_index(drop=True)
    keep = ordered["entry_return_6"].gt(0)

    candidate = evaluate_keep_candidate(
        ordered,
        keep,
        candidate_id="keep_positive_momentum",
        candidate_type="fixture",
        condition={"field": "entry_return_6", "operator": "gt", "value": 0.0},
    )

    assert candidate is not None
    assert candidate["delta_pnl"] > 0
    assert candidate["out_of_sample_delta_pnl"] > 0
    assert candidate["candidate_expectancy"] > candidate["baseline_expectancy"]
    assert candidate["positive_pnl_retention_ratio"] == pytest.approx(1.0)
    assert candidate["decision"] == "PROMOVER_PARA_PAPER_AB"


def test_qlib_ai_threshold_candidates_find_positive_slice() -> None:
    frame = paper_frame(40)
    prepared, _ = prepare_profit_dataset(frame, score_rows=score_rows(frame))
    eligible = prepared.loc[prepared["profit_optimization_eligible"]].reset_index(drop=True)

    candidates = build_score_threshold_candidates(eligible)

    assert candidates
    assert any(item["candidate_type"] == "ai_qlib_threshold" for item in candidates)
    assert any(item["delta_pnl"] > 0 for item in candidates)


def test_combined_feature_and_ai_filter_can_be_ranked() -> None:
    frame = paper_frame(40)
    prepared, _ = prepare_profit_dataset(frame, score_rows=score_rows(frame))
    eligible = prepared.loc[prepared["profit_optimization_eligible"]].reset_index(drop=True)
    singles = build_score_threshold_candidates(eligible)
    feature = evaluate_keep_candidate(
        eligible,
        eligible["entry_return_6"].gt(0),
        candidate_id="positive_momentum",
        candidate_type="entry_feature_filter",
        condition={"field": "entry_return_6", "operator": "gte", "value": 0.0},
    )
    assert feature is not None

    combined = build_combined_filter_candidates(eligible, [feature, *singles[:3]])

    assert combined
    assert all(row["candidate_type"] == "combined_entry_ai_filter" for row in combined)


def test_full_optimizer_ranks_positive_candidate_and_reports_master() -> None:
    frame = paper_frame(40)
    master_rows = [
        {
            "order_id": f"master-{index}",
            "symbol": "ETHUSDT",
            "side": "short",
            "horario_abertura": f"2025-12-{(index % 20) + 1:02d}T00:00:00Z",
            "horario_fechamento": f"2025-12-{(index % 20) + 1:02d}T00:05:00Z",
            "pnl_fechado": 0.5 if index % 2 else -0.2,
        }
        for index in range(20)
    ]

    result = build_profit_maximization(
        frame,
        trader_master_rows=master_rows,
        score_rows=score_rows(frame),
    )

    assert result.report["status"] == "ok"
    assert result.report["baseline_paper_metrics"]["net_pnl"] > 0
    assert result.report["trader_master_metrics"]["trade_count"] == 20
    assert result.report["best_candidate"] is not None
    assert result.report["positive_historical_candidate_found"] is True
    assert result.report["best_candidate"]["decision"] == "PROMOVER_PARA_PAPER_AB"


def test_exit_candidate_is_standardized_into_profit_ranking() -> None:
    frame = paper_frame(40)
    base = profit_metrics(frame)
    exit_candidate = {
        "strategy_id": "capture_more_winner",
        "configuration": {"kind": "fixture"},
        "candidate_net_pnl": float(base["net_pnl"]) + 20.0,
        "delta_pnl": 20.0,
        "candidate_profit_factor": 2.0,
        "candidate_maximum_drawdown": 3.0,
        "out_of_sample_delta_pnl": 8.0,
    }

    result = build_profit_maximization(frame, exit_candidates=[exit_candidate])

    exit_rows = [
        row for row in result.report["ranked_candidates"] if row["candidate_type"] == "exit_policy"
    ]
    assert len(exit_rows) == 1
    assert exit_rows[0]["delta_pnl"] == pytest.approx(20.0)


def test_nan_and_infinite_scores_do_not_pollute_candidates() -> None:
    frame = paper_frame(10)
    rows = [
        {"order_id": "freqtrade-paper-1000", "qlib_score": math.nan},
        {"order_id": "freqtrade-paper-1001", "qlib_score": math.inf},
    ]

    prepared, report = prepare_profit_dataset(frame, score_rows=rows)

    assert report["paper_rows_with_qlib_score"] == 0
    assert prepared["qlib_score"].isna().all()


def test_corrupt_trade_does_not_influence_qlib_rank_universe() -> None:
    frame = paper_frame(12)
    corrupt = frame.iloc[0].copy()
    corrupt["stable_trade_id"] = "freqtrade-paper-653"
    corrupt["trade_id"] = 653
    corrupt["net_pnl"] = 99.0
    source = pd.concat([frame, corrupt.to_frame().T], ignore_index=True)
    scores = score_rows(source)
    scores[-1]["qlib_score"] = 999.0

    prepared, _ = prepare_profit_dataset(source, score_rows=scores)
    baseline, _ = prepare_profit_dataset(frame, score_rows=score_rows(frame))

    corrupt_row = prepared.loc[prepared["trade_id_numeric"].eq(653)].iloc[0]
    actual = prepared.loc[prepared["profit_optimization_eligible"]].set_index(
        "stable_trade_id"
    )["qlib_rank_score"].sort_index()
    expected = baseline.set_index("stable_trade_id")["qlib_rank_score"].sort_index()
    assert pd.isna(corrupt_row["qlib_rank_score"])
    pd.testing.assert_series_equal(actual, expected, check_names=False)


def test_candidate_mask_alignment_is_stable_for_unsorted_frame() -> None:
    frame = paper_frame(40).sample(frac=1.0, random_state=7)
    prepared, _ = prepare_profit_dataset(frame)
    keep = prepared["entry_return_6"].gt(0)

    candidate = evaluate_keep_candidate(
        prepared,
        keep,
        candidate_id="unsorted_positive_momentum",
        candidate_type="fixture",
        condition={"field": "entry_return_6", "operator": "gte", "value": 0.0},
    )

    assert candidate is not None
    assert candidate["delta_pnl"] > 0
    assert candidate["out_of_sample_delta_pnl"] > 0
    assert candidate["positive_pnl_retention_ratio"] == pytest.approx(1.0)


def test_safety_flags_forbid_all_operational_mutation() -> None:
    assert SAFETY_FLAGS == {
        "research_only": True,
        "read_only": True,
        "paper_only": True,
        "operational_authority": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "changes_risk": False,
        "changes_model": False,
        "changes_roi": False,
        "changes_stoploss": False,
        "writes_runtime": False,
        "writes_master": False,
        "writes_sqlite": False,
        "updates_freqtrade": False,
        "updates_risk_manager": False,
        "updates_qlib_runtime": False,
        "updates_ai_shadow_runtime": False,
        "model_promotion_performed": False,
    }
