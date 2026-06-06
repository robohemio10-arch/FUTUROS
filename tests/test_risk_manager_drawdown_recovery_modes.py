from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from smartcrypto.ops.system_healthcheck import run_system_healthcheck
from smartcrypto.risk.risk_recovery_modes import (
    RiskRecoveryLimits,
    run_risk_recovery_mode_audit,
)


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)


def iso(days_ago: int, equity: float) -> dict:
    return {"timestamp": (NOW - timedelta(days=days_ago)).isoformat(), "equity": equity}


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def write_frame(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows)
    if path.suffix == ".parquet":
        frame.to_parquet(path, index=False)
    else:
        frame.to_csv(path, index=False)
    return path


def clean_equity() -> list[dict]:
    return [iso(6, 1000), iso(2, 1010), iso(0, 1020)]


def clean_trades() -> list[dict]:
    return [{"closed_at": (NOW - timedelta(hours=i)).isoformat(), "pnl": value} for i, value in enumerate([3, -1, 2])]


def session_payload(**overrides):
    payload = {
        "status": "ok",
        "backup_status": "pass",
        "restore_status": "pass",
        "clean_streak_days": 5,
        "recovery_approved": False,
        "p0_incidents": 0,
        "p1_incidents": 0,
    }
    payload.update(overrides)
    return payload


def safe_flags() -> dict:
    return {
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
    }


def write_sources(tmp_path: Path, **overrides) -> dict[str, Path]:
    paths = {
        "equity_curve_path": tmp_path / "equity.parquet",
        "closed_trades_path": tmp_path / "closed_trades.csv",
        "paper_session_report_path": tmp_path / "paper_session.json",
        "market_health_report_path": tmp_path / "market_health.json",
        "readiness_report_path": tmp_path / "readiness.json",
        "monte_carlo_report_path": tmp_path / "monte_carlo.json",
        "backtest_report_path": tmp_path / "backtest.json",
        "kill_switch_path": tmp_path / "kill_switch.json",
        "incidents_path": tmp_path / "incidents.json",
        "state_divergence_report_path": tmp_path / "state_divergence.json",
    }
    write_frame(paths["equity_curve_path"], overrides.get("equity_curve", clean_equity()))
    write_frame(paths["closed_trades_path"], overrides.get("closed_trades", clean_trades()))
    write_json(paths["paper_session_report_path"], overrides.get("paper_session", session_payload()))
    write_json(paths["market_health_report_path"], overrides.get("market_health", {"status": "ok", "stale_data_count": 0}))
    write_json(paths["readiness_report_path"], overrides.get("readiness", {"status": "ok", "prediction_stale_block": False}))
    write_json(paths["monte_carlo_report_path"], overrides.get("monte_carlo", {"status": "ok"}))
    write_json(paths["backtest_report_path"], overrides.get("backtest", {"status": "ok"}))
    write_json(paths["kill_switch_path"], overrides.get("kill_switch", {"enabled": False, "status": "inactive"}))
    write_json(paths["incidents_path"], overrides.get("incidents", {"p0": 0, "p1": 0, "open": 0}))
    write_json(paths["state_divergence_report_path"], overrides.get("state_divergence", {"reconciliation_required": False, "divergence_count": 0}))
    return paths


def audit(paths: dict[str, Path], **kwargs):
    return run_risk_recovery_mode_audit(
        **paths,
        report_path=None,
        now=NOW,
        limits=kwargs.pop("limits", RiskRecoveryLimits()),
        **kwargs,
    )


def test_risk_recovery_accepts_normal_conditions(tmp_path):
    report = audit(write_sources(tmp_path))

    assert report["status"] == "ok"
    assert report["recommended_mode"] == "NORMAL"
    assert report["risk_metrics"]["max_drawdown_pct"] == 0.0
    assert report["evidence_quality_summary"]["primary_state"] == "recovery_mode_inactive"
    assert report["evidence_quality_summary"]["operational_evidence_complete"] is True


def test_risk_recovery_enters_conservative_on_warning_conditions(tmp_path):
    report = audit(write_sources(tmp_path, market_health={"status": "warning"}))

    assert report["status"] == "warning"
    assert report["recommended_mode"] == "CONSERVATIVE"
    assert "market_health_warning" in report["warnings"]


def test_risk_recovery_enters_protection_on_drawdown(tmp_path):
    equity = [iso(6, 1000), iso(3, 1100), iso(0, 980)]

    report = audit(write_sources(tmp_path, equity_curve=equity))

    assert report["status"] == "blocked"
    assert report["recommended_mode"] == "PROTECTION"
    assert "max_drawdown_limit_exceeded" in report["blocking_findings"]


def test_risk_recovery_enters_panic_on_critical_block(tmp_path):
    report = audit(write_sources(tmp_path, market_health={"status": "blocked"}))

    assert report["status"] == "blocked"
    assert report["recommended_mode"] == "PANIC"
    assert "market_health_block" in report["blocking_findings"]


def test_risk_recovery_enters_reconciling_on_state_divergence(tmp_path):
    report = audit(write_sources(tmp_path, state_divergence={"reconciliation_required": True, "divergence_count": 1}))

    assert report["status"] == "blocked"
    assert report["recommended_mode"] == "RECONCILING"
    assert "reconciliation_required" in report["blocking_findings"]


def test_risk_recovery_blocks_daily_loss(tmp_path):
    equity = [iso(1, 1000), iso(0, 960)]

    report = audit(write_sources(tmp_path, equity_curve=equity))

    assert report["status"] == "blocked"
    assert "daily_loss_limit_exceeded" in report["blocking_findings"]


def test_risk_recovery_blocks_weekly_loss(tmp_path):
    equity = [iso(6, 1000), iso(0, 920)]

    report = audit(write_sources(tmp_path, equity_curve=equity))

    assert report["status"] == "blocked"
    assert "weekly_loss_limit_exceeded" in report["blocking_findings"]


def test_risk_recovery_blocks_consecutive_losses(tmp_path):
    trades = [{"pnl": -1}, {"pnl": -2}, {"pnl": -3}, {"pnl": -4}, {"pnl": -5}]

    report = audit(write_sources(tmp_path, closed_trades=trades))

    assert report["status"] == "blocked"
    assert report["risk_metrics"]["consecutive_losses"] == 5
    assert "consecutive_losses_limit_exceeded" in report["blocking_findings"]


def test_risk_recovery_blocks_market_health_blocked(tmp_path):
    report = audit(write_sources(tmp_path, market_health={"status": "blocked", "stale_data_count": 1}))

    assert report["status"] == "blocked"
    assert "market_health_block" in report["blocking_findings"]
    assert "stale_data_block" in report["blocking_findings"]


def test_risk_recovery_blocks_kill_switch(tmp_path):
    report = audit(write_sources(tmp_path, kill_switch={"enabled": True, "status": "active"}))

    assert report["status"] == "blocked"
    assert report["recommended_mode"] == "PANIC"
    assert "kill_switch_active" in report["blocking_findings"]


def test_risk_recovery_blocks_p0_p1_incidents(tmp_path):
    report = audit(write_sources(tmp_path, incidents={"p0": 1, "p1": 1, "open": 2}))

    assert report["status"] == "blocked"
    assert "incident_block" in report["blocking_findings"]


def test_risk_recovery_does_not_auto_return_from_panic_to_normal(tmp_path):
    report = audit(write_sources(tmp_path), previous_mode="PANIC")

    assert report["status"] == "ok"
    assert report["recommended_mode"] == "PANIC"
    assert report["transition_reason"] == "panic_requires_explicit_recovery"


def test_risk_recovery_never_allows_ai_to_increase_risk(tmp_path):
    report = audit(write_sources(tmp_path))

    assert "increase_risk" in report["blocked_actions"]
    assert "increase_stake" in report["blocked_actions"]
    assert "increase_leverage" in report["blocked_actions"]
    assert all("increase" not in action for action in report["allowed_actions"])


def test_risk_recovery_blocks_unsafe_safety_flags(tmp_path):
    report = run_risk_recovery_mode_audit(
        **write_sources(tmp_path),
        report_path=None,
        now=NOW,
        safety_overrides={"live_trading_enabled": True, "order_submission_enabled": True},
    )

    assert report["status"] == "blocked"
    assert "unsafe_safety_flag:live_trading_enabled" in report["blocking_findings"]
    assert "unsafe_safety_flag:order_submission_enabled" in report["blocking_findings"]


def test_risk_recovery_differentiates_missing_runtime_sources(tmp_path):
    report = run_risk_recovery_mode_audit(report_path=None, now=NOW)

    assert report["status"] == "missing_data"
    assert report["evidence_quality_summary"]["primary_state"] == "missing_runtime_sources"
    assert set(report["missing_sources"]) >= {"equity_curve", "closed_trades", "market_health_report"}
    assert report["required_sources_missing"] == []
    assert report["paper_only"] is True
    assert report["live_trading_enabled"] is False


def test_risk_recovery_policy_present_without_incidents_is_not_full_ok(tmp_path):
    market = write_json(tmp_path / "market_health.json", {"status": "ok", "stale_data_count": 0})
    incidents = write_json(tmp_path / "incidents.json", {"p0": 0, "p1": 0, "open": 0})

    report = run_risk_recovery_mode_audit(
        market_health_report_path=market,
        incidents_path=incidents,
        report_path=None,
        now=NOW,
    )

    assert report["status"] in {"missing_data", "warning"}
    assert report["recommended_mode"] == "NORMAL"
    assert report["evidence_quality_summary"]["primary_state"] == "market_health_ok_but_no_recovery_state"
    assert report["evidence_quality_summary"]["no_incidents_observed"] is True
    assert report["evidence_quality_summary"]["recovery_mode_inactive"] is True
    assert report["evidence_quality_summary"]["operational_evidence_complete"] is False
    assert "provide_equity_curve_or_closed_trades_snapshot" in report["next_required_actions"]


def test_risk_recovery_reports_source_read_failures_as_invalid_state(tmp_path):
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{not-json", encoding="utf-8")

    report = run_risk_recovery_mode_audit(
        paper_session_report_path=invalid,
        report_path=None,
        now=NOW,
    )

    assert report["status"] == "blocked"
    assert report["evidence_quality_summary"]["primary_state"] == "recovery_state_invalid"
    assert "paper_session_report" in report["evidence_quality_summary"]["blocked_sources"]


def test_cli_run_risk_recovery_mode_audit_runs_successfully(tmp_path):
    paths = write_sources(tmp_path / "sources")
    report_path = tmp_path / "risk_recovery.json"

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_risk_recovery_mode_audit.py"),
            "--equity-curve",
            str(paths["equity_curve_path"]),
            "--closed-trades",
            str(paths["closed_trades_path"]),
            "--paper-session-report",
            str(paths["paper_session_report_path"]),
            "--market-health-report",
            str(paths["market_health_report_path"]),
            "--readiness-report",
            str(paths["readiness_report_path"]),
            "--monte-carlo-report",
            str(paths["monte_carlo_report_path"]),
            "--backtest-report",
            str(paths["backtest_report_path"]),
            "--kill-switch",
            str(paths["kill_switch_path"]),
            "--incidents",
            str(paths["incidents_path"]),
            "--state-divergence-report",
            str(paths["state_divergence_report_path"]),
            "--report",
            str(report_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["recommended_mode"] == "NORMAL"
    assert report_path.exists()


def test_does_not_touch_training_dataset_or_trades_master(tmp_path):
    trades_master = tmp_path / "trades_master.parquet"
    training_dataset = tmp_path / "training_dataset.parquet"
    write_frame(trades_master, [{"timestamp": NOW.isoformat(), "equity": 1}])
    write_frame(training_dataset, [{"timestamp": NOW.isoformat(), "feature": 1}])
    before = {trades_master: trades_master.read_bytes(), training_dataset: training_dataset.read_bytes()}

    audit(write_sources(tmp_path / "sources"))

    assert {path: path.read_bytes() for path in before} == before


def test_does_not_touch_registry_models_signal_producer_or_freqtrade(tmp_path):
    sentinels = [
        tmp_path / "model_registry.json",
        tmp_path / "shadow_model.pkl",
        tmp_path / "active_freqtrade_signals.json",
        tmp_path / "tradesv3.paper.sqlite",
    ]
    for sentinel in sentinels:
        sentinel.write_text(f"sentinel:{sentinel.name}", encoding="utf-8")
    before = {path: path.read_text(encoding="utf-8") for path in sentinels}

    audit(write_sources(tmp_path / "sources"))

    assert {path: path.read_text(encoding="utf-8") for path in sentinels} == before


def test_system_healthcheck_blocks_on_no_trade_readiness_soak_not_ledger_or_recovery_warnings(tmp_path):
    reports = tmp_path / "reports"
    timestamp = NOW.isoformat().replace("+00:00", "Z")
    readiness = write_json(
        reports / "readiness.json",
        {
            "status": "blocked",
            "readiness_approved": False,
            "no_trade_policy_present": True,
            "readiness_blockers": ["no_trade_policy_active", "soak_days_below_required"],
            "generated_at_utc": timestamp,
            **safe_flags(),
        },
    )
    soak = write_json(
        reports / "soak.json",
        {
            "status": "insufficient_soak",
            "readiness_blockers": ["monte_carlo_no_trade_policy_active", "soak_days_below_required"],
            "no_trade_policy_present": True,
            "generated_at_utc": timestamp,
            **safe_flags(),
        },
    )
    critical = write_json(reports / "critical.json", {"status": "ok", "critical_alerts": 0, "generated_at_utc": timestamp, **safe_flags()})
    risk = write_json(
        reports / "risk.json",
        {
            "status": "warning",
            "reason": "market_health_ok_but_no_recovery_state",
            "recommended_mode": "NORMAL",
            "generated_at_utc": timestamp,
            **safe_flags(),
        },
    )
    market = write_json(reports / "market.json", {"status": "ok", "generated_at_utc": timestamp, **safe_flags()})
    state = write_json(
        reports / "state.json",
        {"status": "ok", "reconciliation_required": False, "generated_at_utc": timestamp, **safe_flags()},
    )
    ledger = write_json(
        reports / "ledger.json",
        {
            "status": "warning",
            "reason": "repository_present_but_no_events",
            "generated_at_utc": timestamp,
            **safe_flags(),
        },
    )
    backup = write_json(reports / "backup.json", {"status": "ok", "generated_at_utc": timestamp, **safe_flags()})
    restore = write_json(reports / "restore.json", {"status": "ok", "generated_at_utc": timestamp, **safe_flags()})

    report = run_system_healthcheck(
        readiness_report=readiness,
        paper_soak_report=soak,
        critical_alerting_report=critical,
        risk_recovery_report=risk,
        market_health_report=market,
        state_reconciliation_report=state,
        ledger_report=ledger,
        backup_report=backup,
        restore_report=restore,
        dockerfile=None,
        compose_file=None,
        report_path=None,
        now=NOW,
    )

    assert report["status"] == "blocked"
    assert "readiness_gate_blocked" in report["blocking_findings"]
    assert "no_trade_policy_active" in report["blocking_findings"]
    assert "soak_days_below_required" in report["blocking_findings"]
    assert "ledger_audit_blocked" not in report["blocking_findings"]
    assert "risk_recovery_mode_panic" not in report["blocking_findings"]
    assert report["live_trading_enabled"] is False
