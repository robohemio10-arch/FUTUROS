from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from smartcrypto.execution.paper_profitability_policy_v1 import (
    build_minimum_decision_ledger_context,
    decide_direction,
    evaluate_candidate_policy,
)
from smartcrypto.execution.signal_producer import select_prediction_rows
from smartcrypto.research.paper_profitability_core import (
    SCENARIO_MATRIX,
    evaluate_paper_candidate_profile_preflight,
    evaluate_paper_profitability_core,
    evaluate_scenario_matrix,
)
from smartcrypto.research.paper_profitability_core.evaluator import (
    align_features_point_in_time,
    evaluate_one_scenario,
)
from smartcrypto.risk.risk_manager import RiskLimits, RiskManager

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("probability", "expected"),
    [(0.65, "long"), (0.35, "short"), (0.50, "no_trade"), (0.54, "no_trade")],
)
def test_prob_up_is_the_only_direction_authority(
    probability: float, expected: str
) -> None:
    decision = decide_direction(
        probability, long_probability=0.55, short_probability=0.45
    )
    assert decision.proposed_side == expected
    assert decision.score == pytest.approx((2 * probability) - 1)
    assert decision.confidence == pytest.approx(abs(probability - 0.5))


def test_top_n_does_not_authorize_neutral_probabilities() -> None:
    frame = pd.DataFrame(
        {
            "symbol": ["BTCUSDT", "ETHUSDT"],
            "pair": ["BTC/USDT:USDT", "ETH/USDT:USDT"],
            "prob_up": [0.51, 0.49],
            "score": [999.0, -999.0],
            "confidence": [999.0, 999.0],
        }
    )
    selected = select_prediction_rows(
        frame,
        {
            "policy": {
                "long_probability": 0.55,
                "short_probability": 0.45,
                "max_signals": 2,
                "top_n_telemetry": 2,
            }
        },
    )
    assert selected.empty


def test_runtime_candidate_regime_gate_blocks_only_countertrend_rows() -> None:
    frame = pd.DataFrame(
        {
            "symbol": ["A", "B", "C", "D"],
            "pair": ["A/USDT:USDT", "B/USDT:USDT", "C/USDT:USDT", "D/USDT:USDT"],
            "prob_up": [0.40, 0.40, 0.60, 0.60],
            "market_regime": ["trend_up", "trend_down", "trend_down", "trend_up"],
            "market_regime_status": ["point_in_time"] * 4,
        }
    )
    selected = select_prediction_rows(
        frame,
        {
            "policy": {
                "long_probability": 0.55,
                "short_probability": 0.45,
                "regime_gate_enabled": True,
                "cooldown_minutes": 0,
                "top_n_can_authorize_trade": False,
                "max_signals": 4,
            }
        },
    )
    assert selected[["symbol", "proposed_side"]].to_dict("records") == [
        {"symbol": "B", "proposed_side": "short"},
        {"symbol": "D", "proposed_side": "long"},
    ]


def test_versioned_paper_candidate_profile_preflight_is_ready() -> None:
    report = evaluate_paper_candidate_profile_preflight(project_root=ROOT)
    assert report["status"] == "ok"
    assert report["paper_candidate_profile"] == "READY"
    assert report["long_threshold"] == 0.55
    assert report["short_threshold"] == 0.45
    assert report["regime_gate"] is True
    assert report["cooldown_minutes"] == 0
    assert report["top_n_authorization"] is False
    assert report["decision_ledger"] is True
    assert report["decision_ledger_configured"] is True
    assert report["write_performed"] is False


def test_snapshot_sanity_preflight_keeps_btc_long_and_eth_no_trade() -> None:
    report = evaluate_paper_candidate_profile_preflight(project_root=ROOT)
    btc = report["snapshot_sanity_checks"]["BTCUSDT"]
    eth = report["snapshot_sanity_checks"]["ETHUSDT"]
    assert btc["prob_up"] == pytest.approx(0.6093397332227777)
    assert btc["proposed_side"] == "long"
    assert btc["passed"] is True
    assert eth["prob_up"] == pytest.approx(0.5072025956314988)
    assert eth["proposed_side"] == "no_trade"
    assert eth["final_decision"] == "NO_TRADE"
    assert eth["passed"] is True


@pytest.mark.parametrize(
    ("side", "regime", "blocked"),
    [
        ("short", "trend_up", True),
        ("short", "trend_up_high_vol", True),
        ("long", "trend_down", True),
        ("long", "trend_down_high_vol", True),
        ("long", "trend_up", False),
    ],
)
def test_regime_gate_blocks_only_declared_counter_trend_cases(
    side: str, regime: str, blocked: bool
) -> None:
    result = evaluate_candidate_policy(
        proposed_side=side,
        market_regime=regime,
        market_regime_status="point_in_time",
        regime_gate_enabled=True,
        observed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        cooldown_until=None,
    )
    assert result.regime_block is blocked


def test_unknown_regime_is_explicit_and_fail_closed_when_gate_is_on() -> None:
    result = evaluate_candidate_policy(
        proposed_side="long",
        market_regime="unknown",
        market_regime_status="stale",
        regime_gate_enabled=True,
        observed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        cooldown_until=None,
    )
    assert result.regime_block is True
    assert result.regime_block_reason == "market_regime_unknown_or_stale"


def test_risk_manager_never_inverts_proposed_side() -> None:
    manager = RiskManager(RiskLimits(runtime_mode="paper", allowed_pairs=("BTCUSDT",)))
    decision = manager.approve(
        {
            "symbol": "BTCUSDT",
            "side": "long",
            "proposed_side": "long",
            "prob_up": 0.65,
            "score": -999.0,
        }
    )
    assert decision.approved is True
    assert decision.signal["side"] == "long"
    assert decision.signal["final_decision"] == "ALLOW"


def test_risk_manager_blocks_side_mismatch_instead_of_inverting() -> None:
    manager = RiskManager(RiskLimits(runtime_mode="paper", allowed_pairs=("BTCUSDT",)))
    decision = manager.approve(
        {"symbol": "BTCUSDT", "side": "short", "proposed_side": "long"}
    )
    assert decision.approved is False
    assert "side_does_not_match_proposed_side" in decision.reasons


def test_minimum_ledger_context_contains_required_fields() -> None:
    signal = {
        "generated_at": "2026-08-01T00:00:00Z",
        "symbol": "BTCUSDT",
        "prob_up": 0.65,
        "score": 0.3,
        "confidence": 0.15,
        "proposed_side": "long",
        "market_regime": "trend_up",
        "regime_block": False,
        "cooldown_block": False,
        "signal_id": "signal-1",
        "decision_event_id": "decision-1",
    }
    context = build_minimum_decision_ledger_context(
        signal, final_decision="ALLOW", risk_approved=True
    )
    assert set(context) == {
        "timestamp",
        "symbol",
        "prob_up",
        "score",
        "confidence",
        "proposed_side",
        "market_regime",
        "regime_block",
        "cooldown_block",
        "risk_approved",
        "final_decision",
        "signal_id",
        "decision_event_id",
        "trade_id",
    }


def test_scenario_matrix_is_exactly_the_required_24() -> None:
    assert len(SCENARIO_MATRIX) == 24
    assert {row[:2] for row in SCENARIO_MATRIX} == {
        (0.55, 0.45),
        (0.60, 0.40),
        (0.65, 0.35),
    }
    assert {row[2] for row in SCENARIO_MATRIX} == {False, True}
    assert {row[3] for row in SCENARIO_MATRIX} == {0, 5, 15, 30}


def test_evaluator_is_deterministic_for_same_rows() -> None:
    frame = _scenario_frame()
    assert evaluate_scenario_matrix(frame) == evaluate_scenario_matrix(frame)


def test_cooldown_starts_only_after_stop_close_without_lookahead() -> None:
    frame = _scenario_frame(
        opens=("2026-08-01T00:00:00Z", "2026-08-01T00:03:00Z", "2026-08-01T00:11:00Z"),
        closes=("2026-08-01T00:10:00Z", "2026-08-01T00:04:00Z", "2026-08-01T00:12:00Z"),
        exit_reasons=("stop_loss", "roi", "roi"),
    )
    result = evaluate_one_scenario(
        frame,
        long_probability=0.55,
        short_probability=0.45,
        regime_gate_enabled=False,
        cooldown_minutes=5,
    )
    assert result["trade_count"] == 2
    assert result["no_trade_count"] == 1


def test_point_in_time_alignment_never_uses_unclosed_candle() -> None:
    trades = pd.DataFrame(
        {
            "id": [1],
            "symbol": ["BTCUSDT"],
            "open_time_utc": pd.to_datetime(["2026-08-01T00:03:00Z"], utc=True),
            "accounting_valid": [True],
        }
    )
    market = pd.DataFrame(
        {
            "symbol": ["BTCUSDT", "BTCUSDT"],
            "tf": ["5m", "5m"],
            "ts": pd.to_datetime(
                ["2026-07-31T23:55:00Z", "2026-08-01T00:00:00Z"], utc=True
            ),
            "close": [100.0, 999.0],
        }
    )
    aligned, report = align_features_point_in_time(trades, market)
    assert aligned.iloc[0]["close"] == 100.0
    assert report["lookahead_violation_count"] == 0


def test_default_evaluator_is_no_write_and_fail_closed(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    report = evaluate_paper_profitability_core(
        project_root=tmp_path,
        paper_db_path=tmp_path / "missing.sqlite",
        output_path=output,
    )
    assert report["status"] == "blocked"
    assert report["write_performed"] is False
    assert output.exists() is False
    assert report["candidate_eligible"] is False


def test_explicit_report_write_is_restricted_to_project_data_reports(
    tmp_path: Path,
) -> None:
    output = tmp_path / "outside.json"
    report = evaluate_paper_profitability_core(
        project_root=tmp_path,
        paper_db_path=tmp_path / "missing.sqlite",
        output_path=output,
        write_report=True,
    )
    assert report["status"] == "blocked"
    assert report["reason"] == "report_output_path_outside_data_reports"
    assert report["write_performed"] is False
    assert output.exists() is False


def test_cli_no_write_json_executes_without_runtime_write(tmp_path: Path) -> None:
    command = [
        sys.executable,
        str(ROOT / "scripts" / "evaluate_paper_profitability_core_v1.py"),
        "--project-root",
        str(tmp_path),
        "--paper-db",
        str(tmp_path / "missing.sqlite"),
        "--no-write",
        "--json",
    ]
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    payload = json.loads(completed.stdout)
    assert payload["status"] == "blocked"
    assert payload["runtime_write"] is False
    assert payload["sends_orders"] is False


def test_cli_profile_preflight_is_no_write_and_executes() -> None:
    command = [
        sys.executable,
        str(ROOT / "scripts" / "evaluate_paper_profitability_core_v1.py"),
        "--project-root",
        str(ROOT),
        "--profile-preflight-only",
        "--json",
    ]
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    payload = json.loads(completed.stdout)
    assert payload["paper_candidate_profile"] == "READY"
    assert payload["snapshot_sanity_checks"]["BTCUSDT"]["proposed_side"] == "long"
    assert payload["snapshot_sanity_checks"]["ETHUSDT"]["final_decision"] == "NO_TRADE"
    assert payload["runtime_execution_performed"] is False


def _scenario_frame(
    *,
    opens: tuple[str, ...] = (
        "2026-08-01T00:00:00Z",
        "2026-08-01T00:05:00Z",
        "2026-08-01T00:10:00Z",
    ),
    closes: tuple[str, ...] = (
        "2026-08-01T00:01:00Z",
        "2026-08-01T00:06:00Z",
        "2026-08-01T00:11:00Z",
    ),
    exit_reasons: tuple[str, ...] = ("roi", "stop_loss", "roi"),
) -> pd.DataFrame:
    rows = len(opens)
    return pd.DataFrame(
        {
            "id": range(1, rows + 1),
            "symbol": ["BTCUSDT"] * rows,
            "prob_up": [0.70] * rows,
            "market_regime": ["trend_up"] * rows,
            "market_regime_status": ["point_in_time"] * rows,
            "actual_side": ["long"] * rows,
            "candidate_net_pnl_long": [1.0, -1.0, 1.0][:rows],
            "candidate_net_pnl_short": [-1.0, 1.0, -1.0][:rows],
            "baseline_net_pnl": [-1.0] * rows,
            "exit_reason": list(exit_reasons),
            "open_time_utc": pd.to_datetime(list(opens), utc=True),
            "close_time_utc": pd.to_datetime(list(closes), utc=True),
            "fold_id": [1, 2, 3][:rows],
        }
    )
