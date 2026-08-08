from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from smartcrypto.learning.paper_autotrain_daily_quarantine_activation.activation import QuarantinePaths
from smartcrypto.learning.paper_autotrain_financial_objective.objective import (
    FINANCIAL_OBJECTIVES,
    KNOWN_FINANCIAL_SAMPLE_INVALID_IDS,
    FinancialObjectiveTrainerBackend,
    build_financial_objective,
    build_profit_aware_daily_autotrain,
)


def microbatch() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for index in range(30):
        trade_id = 141 if index == 0 else 1000 + index
        profitable = index % 3 != 0
        rows.append(
            {
                "order_id": trade_id,
                "close_time_utc": pd.Timestamp("2026-01-01T00:00:00Z")
                + pd.Timedelta(minutes=index * 10 + 5),
                "feature_momentum": 1.0 if profitable else -1.0,
                "feature_volatility": 0.10 + index / 1000,
                "target_profitable": 1 if profitable else 0,
                "target_return": 2.0 if profitable else -1.5,
                "qlib_score": 0.90 if profitable else 0.10,
                "ai_shadow_probability": 0.85 if profitable else 0.15,
            }
        )
    return pd.DataFrame(rows)


def profit_dataset() -> pd.DataFrame:
    frame = microbatch().copy()
    frame["trade_id"] = frame["order_id"]
    frame["symbol"] = ["ETHUSDT" if index % 2 else "BTCUSDT" for index in range(len(frame))]
    frame["side"] = ["long" if index % 4 == 0 else "short" for index in range(len(frame))]
    frame["open_time_utc"] = pd.date_range(
        "2026-01-01", periods=len(frame), freq="10min", tz="UTC"
    )
    frame["close_time_utc"] = frame["open_time_utc"] + pd.Timedelta(minutes=5)
    frame["duration_seconds"] = 300.0
    frame["stake_amount"] = 10.0
    frame["net_pnl"] = frame["target_return"]
    frame["max_unrealized_profit"] = [
        4.0 if value > 0 else (1.0 if index % 2 else 0.0)
        for index, value in enumerate(frame["net_pnl"])
    ]
    frame["mfe_pct"] = frame["max_unrealized_profit"] / 100.0
    frame["mae_pct"] = [-0.005 if value > 0 else -0.02 for value in frame["net_pnl"]]
    frame["time_to_mfe_seconds"] = 60.0
    frame["time_to_mae_seconds"] = 120.0
    frame["hour_utc"] = frame["open_time_utc"].dt.hour
    frame["day_of_week"] = frame["open_time_utc"].dt.day_name()
    frame["duration_bucket"] = "lte_15m"
    frame["regime"] = ["trend" if value > 0 else "chop" for value in frame["net_pnl"]]
    frame["pre_entry_rsi"] = [60.0 if value > 0 else 35.0 for value in frame["net_pnl"]]
    frame["pre_entry_atr_pct"] = 0.01
    frame["pre_entry_trend_score"] = [1.0 if value > 0 else -1.0 for value in frame["net_pnl"]]
    frame["pre_entry_return_5"] = [0.01 if value > 0 else -0.01 for value in frame["net_pnl"]]
    frame["pre_entry_volume_rel_30"] = 1.0
    frame["analysis_eligible"] = True
    frame["exit_reason"] = ["roi" if value > 0 else "stop_loss" for value in frame["net_pnl"]]
    return frame


def objective(*, exits: list[dict[str, object]] | None = None):
    return build_financial_objective(
        ".",
        microbatch_frame=microbatch(),
        profit_dataset_frame=profit_dataset(),
        profit_report={
            "status": "ok",
            "reason": "fixture",
            "candidate_exit_changes": exits or [],
        },
        trader_master_rows=[],
        score_rows=[],
    )


def test_financial_objective_excludes_double_exit_and_weights_money() -> None:
    result = objective()

    assert 141 in KNOWN_FINANCIAL_SAMPLE_INVALID_IDS
    assert 141 not in set(result.microbatch["order_id"].astype(int))
    assert result.summary["financial_sample_invalid_count"] == 1
    assert result.summary["objective_priority"] == list(FINANCIAL_OBJECTIVES)
    assert result.microbatch["financial_sample_weight"].between(0.25, 5.0).all()
    assert result.microbatch["financial_objective_classification"].str.contains("learning").any()


def test_winner_capture_and_loser_paths_feed_daily_learning() -> None:
    result = objective()
    summary = result.summary

    assert summary["winner_capture"]["winner_capture_ratio"] == pytest.approx(0.5)
    assert summary["winner_capture"]["profit_left_on_table"] > 0
    classes = summary["loser_analysis"]["classification_counts"]
    assert classes["profit_protection_exit_candidate"] > 0
    assert classes["entry_filter_candidate"] > 0


def test_qlib_and_ai_shadow_thresholds_optimize_financial_metrics_oos() -> None:
    result = objective()
    rows = result.summary["entry_filter_candidates"]
    score_candidates = [
        row
        for row in rows
        if row["condition"].get("field") in {"qlib_score", "ai_shadow_probability"}
    ]

    assert score_candidates
    assert any(float(row["delta_pnl"]) > 0 for row in score_candidates)
    assert any(float(row["out_of_sample_delta_pnl"]) > 0 for row in score_candidates)
    assert result.summary["best_candidate"] is not None
    assert "winner_capture_ratio" in result.summary["best_candidate"]


def test_combined_entry_and_model_candidates_are_generated() -> None:
    result = objective()
    combined = result.summary["combined_filter_candidates"]

    assert combined
    assert all(row["candidate_type"] == "combined_entry_ai_filter" for row in combined)
    assert any(
        any(
            condition.get("field") in {"qlib_score", "ai_shadow_probability"}
            for condition in row["condition"]["conditions"]
        )
        for row in combined
    )


def test_exit_joint_candidate_requires_combined_replay_instead_of_faking_pnl() -> None:
    result = objective(
        exits=[
            {
                "strategy_id": "fixed_tp",
                "configuration": {"kind": "fixed_tp_sl"},
                "delta_pnl": 5.0,
                "out_of_sample_delta_pnl": 2.0,
            }
        ]
    )

    joints = result.summary["joint_profit_candidates"]
    assert joints
    assert all(row["combined_net_pnl_claimed"] is False for row in joints)
    assert all(
        row["decision"] == "REPLAY_COMBINADO_OBRIGATORIO_ANTES_DE_PAPER"
        for row in joints
    )


def test_joint_entry_ai_and_exit_policy_is_simulated_on_same_universe() -> None:
    frame = profit_dataset().copy()
    frame["entry_price"] = 100.0
    frame["exit_price"] = [102.0 if value > 0 else 98.5 for value in frame["net_pnl"]]
    frame["quantity"] = 1.0
    frame["contract_size"] = 1.0
    frame["fees"] = 0.0
    frame["funding"] = 0.0
    frame["candle_timeframe"] = "1m"
    candle_rows: list[dict[str, object]] = []
    start = frame["open_time_utc"].min()
    end = frame["close_time_utc"].max()
    for symbol in ("BTCUSDT", "ETHUSDT"):
        for timestamp in pd.date_range(start, end, freq="1min", tz="UTC"):
            candle_rows.append(
                {
                    "symbol": symbol,
                    "tf": "1m",
                    "ts": timestamp,
                    "open": 100.0,
                    "high": 101.2,
                    "low": 99.6,
                    "close": 100.8,
                }
            )
    candles = pd.DataFrame(candle_rows)
    result = build_financial_objective(
        ".",
        microbatch_frame=microbatch(),
        profit_dataset_frame=frame,
        profit_report={
            "status": "ok",
            "reason": "fixture",
            "candidate_exit_changes": [
                {
                    "strategy_id": "fixed_tp_100_sl_50_bps",
                    "configuration": {
                        "strategy_id": "fixed_tp_100_sl_50_bps",
                        "kind": "fixed_tp_sl",
                        "tp": 0.010,
                        "sl": 0.005,
                    },
                    "delta_pnl": 1.0,
                    "out_of_sample_delta_pnl": 1.0,
                }
            ],
        },
        trader_master_rows=[],
        score_rows=[],
        candles_frame=candles,
    )

    joints = result.summary["joint_profit_candidates"]
    assert joints
    assert all(row["combined_net_pnl_claimed"] is True for row in joints)
    assert all(row["candidate_type"] == "entry_ai_plus_exit_simulation" for row in joints)
    assert all("candidate_net_pnl" in row for row in joints)
    assert all("out_of_sample_delta_pnl" in row for row in joints)


def test_weighted_trainer_uses_financial_sample_weight(tmp_path: Path) -> None:
    frame = microbatch().loc[lambda data: data["order_id"].ne(141)].copy()
    frame["financial_sample_weight"] = [
        1.0 + index / 100 for index in range(len(frame))
    ]
    paths = QuarantinePaths(
        report_json=tmp_path / "report.json",
        report_markdown=tmp_path / "report.md",
        research_dir=tmp_path / "research",
        model_dir=tmp_path / "models",
        registry_path=tmp_path / "registry.json",
        feedback_events_path=tmp_path / "feedback.jsonl",
        microbatch_snapshot_path=tmp_path / "microbatch.parquet",
        last_run_state_path=tmp_path / "last.json",
        watermark_path=tmp_path / "watermark.json",
    )

    result = FinancialObjectiveTrainerBackend().train_challenger(
        root=tmp_path,
        run_id="fixture",
        backend_id="ai_shadow",
        microbatch=frame,
        paths=paths,
        write_artifact=False,
    )

    assert result["status"] == "trained_quarantine_only"
    candidate = result["candidate"]
    assert candidate["financial_objective_applied"] is True
    assert candidate["financial_sample_weight_mean"] > 1.0
    assert candidate["promotion_eligible"] is False


def test_deployed_daily_foundation_path_uses_financial_objective_without_runtime_change(
    tmp_path: Path,
) -> None:
    from smartcrypto.learning.paper_autolearning.daily_foundation_runner import (
        build_paper_autolearning_foundation_report,
    )

    def closed(order_id: int, pnl: float) -> dict[str, object]:
        return {
            "order_id": str(order_id),
            "internal_order_id": "",
            "trade_id": f"trade_{order_id}",
            "moeda": "BTCUSDT",
            "fechar_side": "long",
            "horario_abertura": "2026-07-01T12:00:00Z",
            "horario_fechamento": "2026-07-01T12:10:00Z",
            "preco_abertura": 100.0,
            "preco_fechamento": 101.0 if pnl > 0 else 99.0,
            "quantity": 1.0,
            "notional": 100.0,
            "pnl_fechado": pnl,
            "taxa_lucros_perdas_fechados_pct": pnl,
            "trading_fee": 0.04,
            "funding_fee": 0.01,
            "leverage": 2,
            "margin_mode": "isolated",
            "liquidation_price": 80.0,
            "exit_reason": "roi" if pnl > 0 else "stop_loss",
        }

    report = build_paper_autolearning_foundation_report(
        project_root=tmp_path,
        closed_trade_rows=[closed(141, 99.0), closed(2001, 2.0), closed(2002, -1.0)],
        write_feedback=False,
        train_smoke=True,
    )

    assert report["status"] == "ok"
    assert report["financial_objective_applied_to_microbatch"] is True
    assert report["financial_sample_invalid_count"] == 1
    assert report["microbatch_rows"] == 2
    assert report["financial_sample_weight_mean"] is not None
    assert report["financial_training_blocked"] is False
    assert report["model_promotion_performed"] is False
    assert report["active_model_changed"] is False
    assert report["sends_orders"] is False
    assert report["exchange_private_access"] is False
    assert report["changes_risk"] is False


def test_daily_quarantine_autotrain_report_embeds_profit_objective_and_keeps_runtime_locked() -> None:
    closed = pd.DataFrame(
        [
            {
                "order_id": "2001",
                "symbol": "BTCUSDT",
                "side": "long",
                "open_time_utc": "2026-01-01T00:00:00Z",
                "close_time_utc": "2026-01-01T00:05:00Z",
                "net_pnl": 2.0,
            },
            {
                "order_id": "2002",
                "symbol": "ETHUSDT",
                "side": "short",
                "open_time_utc": "2026-01-01T00:10:00Z",
                "close_time_utc": "2026-01-01T00:15:00Z",
                "net_pnl": -1.0,
            },
        ]
    )
    report = build_profit_aware_daily_autotrain(
        ".",
        once=True,
        closed_trades_frame=closed,
        microbatch_frame=microbatch(),
    )

    assert report["financial_objectives"] == list(FINANCIAL_OBJECTIVES)
    assert report["profit_objective_applied_to_training"] is True
    assert report["freqtrade_runtime_changed"] is False
    assert report["roi_changed"] is False
    assert report["stoploss_changed"] is False
    assert report["risk_changed"] is False
    assert report["model_active_changed"] is False
    assert report["order_submission_changed"] is False
    assert report["containers_changed"] is False
    assert report["canary_enabled"] is False
    assert report["live_enabled"] is False
    assert report["sends_orders"] is False
