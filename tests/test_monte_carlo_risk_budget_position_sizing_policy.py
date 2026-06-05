from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.risk.monte_carlo_risk_budget_policy import build_monte_carlo_risk_budget_policy


REPO_ROOT = Path(__file__).resolve().parents[1]


def write_report(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def current_blocked_report() -> dict:
    return {
        "status": "blocked",
        "reason": "risk_of_ruin_exceeds_limit",
        "input_rows": 2973,
        "usable_rows": 345,
        "return_column_used": "pnl_fechado",
        "sample_warning": False,
        "risk_metrics": {
            "risk_of_ruin": 0.911,
            "probability_of_loss": 0.932,
            "expectancy_per_trade": -36.0179,
            "simulated_profit_factor": 0.6901,
            "median_final_equity": -2542.33,
            "mean_final_equity": -2601.79,
            "p95_max_drawdown_pct": 728.46,
            "p95_max_losing_streak": 9,
        },
        "simulation_parameters": {
            "initial_capital": 1000,
            "stake": 100,
            "leverage": 1,
            "horizon_trades": 100,
            "simulations": 1000,
        },
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
    }


def acceptable_report() -> dict:
    return {
        "status": "ok",
        "reason": "risk_within_limits",
        "input_rows": 200,
        "usable_rows": 200,
        "risk_metrics": {
            "risk_of_ruin": 0.0,
            "probability_of_loss": 0.2,
            "expectancy_per_trade": 4.2,
            "simulated_profit_factor": 1.8,
            "p95_max_drawdown_pct": 12.0,
        },
        "simulation_parameters": {"initial_capital": 1000, "stake": 100, "leverage": 1},
    }


def build(tmp_path: Path, payload: dict, **kwargs) -> dict:
    source = write_report(tmp_path / "monte_carlo.json", payload)
    return build_monte_carlo_risk_budget_policy(
        monte_carlo_report=source,
        output=tmp_path / "policy.json",
        risk_of_ruin_cap=0.05,
        max_drawdown_cap_pct=40,
        min_profit_factor=1.1,
        min_expectancy=0,
        **kwargs,
    )


def test_policy_blocks_negative_expectancy(tmp_path: Path) -> None:
    payload = acceptable_report()
    payload["risk_metrics"]["expectancy_per_trade"] = -0.01

    report = build(tmp_path, payload)

    assert report["status"] == "blocked"
    assert "expectancy_negative" in report["blocking_findings"]
    assert report["policy_action"] == "no_trade"


def test_policy_blocks_risk_of_ruin_above_cap(tmp_path: Path) -> None:
    payload = acceptable_report()
    payload["risk_metrics"]["risk_of_ruin"] = 0.2

    report = build(tmp_path, payload)

    assert report["status"] == "blocked"
    assert "risk_of_ruin_exceeds_cap" in report["blocking_findings"]


def test_policy_blocks_drawdown_above_cap(tmp_path: Path) -> None:
    payload = acceptable_report()
    payload["risk_metrics"]["p95_max_drawdown_pct"] = 80

    report = build(tmp_path, payload)

    assert report["status"] == "blocked"
    assert "p95_drawdown_exceeds_cap" in report["blocking_findings"]


def test_policy_blocks_profit_factor_below_minimum(tmp_path: Path) -> None:
    payload = acceptable_report()
    payload["risk_metrics"]["simulated_profit_factor"] = 0.9

    report = build(tmp_path, payload)

    assert report["status"] == "blocked"
    assert "profit_factor_below_minimum" in report["blocking_findings"]


def test_policy_recommends_no_trade_for_current_report_shape(tmp_path: Path) -> None:
    report = build(tmp_path, current_blocked_report())

    assert report["status"] == "blocked"
    assert report["policy_action"] == "no_trade"
    assert "expectancy_negative" in report["no_trade_reason"]
    assert "risk_of_ruin_exceeds_cap" in report["no_trade_reason"]
    assert report["max_stake_recommended"] == 0.0
    assert report["readiness_may_proceed"] is False
    assert report["live_release_allowed"] is False


def test_policy_never_allows_live_release(tmp_path: Path) -> None:
    ok_report = build(tmp_path, acceptable_report())
    bad_report = build(tmp_path / "bad", current_blocked_report())

    assert ok_report["status"] == "ok"
    assert ok_report["live_release_allowed"] is False
    assert ok_report["policy_action"] == "eligible_for_shadow_only"
    assert bad_report["live_release_allowed"] is False


def test_policy_never_changes_risk_or_sends_orders(tmp_path: Path) -> None:
    report = build(tmp_path, acceptable_report())

    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["sends_orders"] is False
    assert report["exchange_private_access"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False
    assert report["risk_manager_updated"] is False
    assert report["stake_updated"] is False
    assert report["leverage_updated"] is False
    assert report["signal_producer_updated"] is False
    assert report["model_promoted"] is False
    assert report["freqtrade_db_touched"] is False


def test_policy_computes_conservative_position_sizing(tmp_path: Path) -> None:
    payload = acceptable_report()
    payload["risk_metrics"]["simulated_profit_factor"] = 1.05

    report = build(tmp_path, payload, strict=False)

    assert report["status"] == "warning"
    assert report["policy_action"] == "reduce_risk"
    assert 0 < report["max_stake_recommended"] < 100
    assert 0 < report["max_leverage_recommended"] <= 1
    assert report["daily_loss_cap_recommended"] <= 10
    assert report["weekly_loss_cap_recommended"] <= 30


def test_policy_handles_missing_monte_carlo_report(tmp_path: Path) -> None:
    report = build_monte_carlo_risk_budget_policy(
        monte_carlo_report=tmp_path / "missing.json",
        output=tmp_path / "policy.json",
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "missing_monte_carlo_report"
    assert report["readiness_may_proceed"] is False


def test_policy_handles_missing_nested_metrics(tmp_path: Path) -> None:
    top_level_report = {
        "status": "blocked",
        "risk_of_ruin": 0.91,
        "probability_of_loss": 0.93,
        "expectancy_per_trade": -36.0,
        "simulated_profit_factor": 0.69,
        "p95_max_drawdown_pct": 728.0,
        "initial_capital": 1000,
        "stake": 100,
        "leverage": 1,
    }

    report = build(tmp_path, top_level_report)

    assert report["status"] == "blocked"
    assert report["risk_of_ruin"] == 0.91
    assert "missing_monte_carlo_metrics" not in ";".join(report["blocking_findings"])


def test_cli_build_monte_carlo_risk_budget_policy_runs_successfully(tmp_path: Path) -> None:
    source = write_report(tmp_path / "monte_carlo.json", acceptable_report())
    output = tmp_path / "policy.json"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_monte_carlo_risk_budget_policy.py",
            "--monte-carlo-report",
            str(source),
            "--output",
            str(output),
            "--risk-of-ruin-cap",
            "0.05",
            "--max-drawdown-cap-pct",
            "40",
            "--min-profit-factor",
            "1.1",
            "--min-expectancy",
            "0",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert output.exists()


def test_does_not_touch_freqtrade_db_models_registry_training_dataset_or_trades_master(tmp_path: Path) -> None:
    sentinels = [
        tmp_path / "tradesv3.paper.sqlite",
        tmp_path / "model_registry.json",
        tmp_path / "shadow_model.joblib",
        tmp_path / "training_dataset.parquet",
        tmp_path / "trades_master.xlsx",
        tmp_path / "active_freqtrade_signals.json",
    ]
    for sentinel in sentinels:
        sentinel.write_text(f"sentinel:{sentinel.name}", encoding="utf-8")
    before = {path: path.read_text(encoding="utf-8") for path in sentinels}

    build(tmp_path / "policy", current_blocked_report())

    assert {path: path.read_text(encoding="utf-8") for path in sentinels} == before
