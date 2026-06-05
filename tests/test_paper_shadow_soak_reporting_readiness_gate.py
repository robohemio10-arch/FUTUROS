from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scripts import build_paper_shadow_soak_report as soak_cli
from scripts import run_readiness_gate_audit as gate_cli
from smartcrypto.ops.paper_shadow_soak_report import build_paper_shadow_soak_report
from smartcrypto.ops.readiness_gate import run_readiness_gate_audit

NOW = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)
START = "2026-06-01T12:00:00Z"
END = "2026-06-03T11:00:00Z"


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return path


def write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows), encoding="utf-8")
    return path


def safe_flags() -> dict[str, object]:
    return {
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
    }


def source_paths(tmp_path: Path) -> dict[str, Path]:
    reports = tmp_path / "reports"
    return {
        "financial_event_log": reports / "financial_event_log.jsonl",
        "critical_alerting_report": reports / "critical_alerting_report.json",
        "risk_recovery_report": reports / "risk_recovery_mode_audit_report.json",
        "market_health_report": reports / "market_data_health_audit_report.json",
        "state_reconciliation_report": reports / "state_reconciliation_audit_report.json",
        "ledger_report": reports / "order_intent_capital_ledger_audit_report.json",
        "ai_governance_report": reports / "ai_governance_dashboard_sources_report.json",
        "risk_readiness_report": reports / "risk_readiness_soak_dashboard_sources_report.json",
        "drift_report": reports / "ai_shadow_drift_monitor_report.json",
        "financial_threshold_report": reports / "ai_shadow_financial_threshold_evaluation_report.json",
        "anti_leakage_report": reports / "phase23_anti_leakage_report.json",
        "monte_carlo_report": reports / "monte_carlo_risk_simulation_report.json",
        "monte_carlo_risk_budget_policy_report": reports / "monte_carlo_risk_budget_policy_report.json",
        "event_backtest_report": reports / "event_driven_backtest_report.json",
        "data_quality_report": reports / "data_quality_report.json",
        "dataset_manifest": reports / "dataset_manifest.json",
        "paper_soak_report": reports / "paper_soak_report.json",
        "runtime_safety_report": reports / "runtime_safety_config_validation_report.json",
        "readiness_gate_report": reports / "readiness_gate_report.json",
    }


def default_events() -> list[dict]:
    return [
        {
            "event_type": "signal_generated",
            "occurred_at_utc": START,
            "runtime_mode": "paper",
            "paper_only": True,
            "decision": "PAPER_SIGNAL",
        },
        {
            "event_type": "shadow_decision",
            "occurred_at_utc": END,
            "runtime_mode": "shadow",
            "shadow_only": True,
            "metadata": {"decision": "SHADOW_ENTRY"},
        },
    ]


def write_soak_sources(tmp_path: Path, *, overrides: dict[str, dict] | None = None, events: list[dict] | None = None) -> dict[str, Path]:
    paths = source_paths(tmp_path)
    overrides = overrides or {}
    write_jsonl(paths["financial_event_log"], events or default_events())
    payloads = {
        "critical_alerting_report": {"status": "ok", "critical_alerts": 0, **safe_flags()},
        "risk_recovery_report": {"status": "ok", "recommended_mode": "NORMAL", "blocking_findings": [], "risk_metrics": {"max_drawdown_pct": 1.2}, **safe_flags()},
        "market_health_report": {"status": "ok", "stale_data_count": 0, "high_spread_blocks": 0, "low_liquidity_blocks": 0, "latency_blocks": 0, **safe_flags()},
        "state_reconciliation_report": {"status": "ok", "reconciliation_required": False, "state_divergence_count": 0, **safe_flags()},
        "ledger_report": {"status": "ok", "order_intents_count": 2, "duplicate_idempotency_key_count": 0, "duplicate_client_order_id_count": 0, "dispatch_unknown_count": 0, **safe_flags()},
        "ai_governance_report": {"status": "ok", **safe_flags()},
        "risk_readiness_report": {
            "status": "ok",
            "clean_streak_days": 2,
            "p0_incidents": 0,
            "p1_incidents": 0,
            "p2_incidents": 0,
            "restart_drill_status": "pass",
            "kill_switch_drill_status": "pass",
            "api_timeout_drill_status": "pass",
            "partial_fill_drill_status": "pass",
            "flash_crash_drill_status": "pass",
            **safe_flags(),
        },
        "drift_report": {"status": "ok", "drift_blocks": 0, **safe_flags()},
        "financial_threshold_report": {"status": "ok", "paper_pnl_net": 10.0, "shadow_pnl_net": 8.0, **safe_flags()},
        "anti_leakage_report": {"status": "ok", **safe_flags()},
        "monte_carlo_report": {"status": "ok", **safe_flags()},
        "monte_carlo_risk_budget_policy_report": {"status": "ok", "policy_action": "observe_only", "readiness_may_proceed": True, "live_release_allowed": False, **safe_flags()},
        "event_backtest_report": {"status": "ok", **safe_flags()},
        "data_quality_report": {"status": "ok", **safe_flags()},
        "dataset_manifest": {"status": "ok", "rows": 26, **safe_flags()},
    }
    for name, patch in overrides.items():
        payloads[name] = {**payloads.get(name, {"status": "ok"}), **patch}
    for name, payload in payloads.items():
        write_json(paths[name], payload)
    return paths


def build_soak(tmp_path: Path, *, overrides: dict[str, dict] | None = None, required_days: int = 2, strict: bool = False) -> dict:
    paths = write_soak_sources(tmp_path, overrides=overrides)
    return build_paper_shadow_soak_report(
        financial_event_log=paths["financial_event_log"],
        critical_alerting_report=paths["critical_alerting_report"],
        risk_recovery_report=paths["risk_recovery_report"],
        market_health_report=paths["market_health_report"],
        state_reconciliation_report=paths["state_reconciliation_report"],
        ledger_report=paths["ledger_report"],
        ai_governance_report=paths["ai_governance_report"],
        risk_readiness_report=paths["risk_readiness_report"],
        drift_report=paths["drift_report"],
        financial_threshold_report=paths["financial_threshold_report"],
        anti_leakage_report=paths["anti_leakage_report"],
        monte_carlo_report=paths["monte_carlo_report"],
        monte_carlo_risk_budget_policy_report=paths["monte_carlo_risk_budget_policy_report"],
        event_backtest_report=paths["event_backtest_report"],
        data_quality_report=paths["data_quality_report"],
        dataset_manifest=paths["dataset_manifest"],
        report_path=paths["paper_soak_report"],
        required_soak_days=required_days,
        strict=strict,
        now=NOW,
    )


def run_gate(tmp_path: Path, *, strict: bool = False, required_days: int = 2, omit_runtime_safety: bool = False) -> dict:
    paths = source_paths(tmp_path)
    if not paths["paper_soak_report"].exists():
        build_soak(tmp_path, required_days=required_days, strict=False)
    if not omit_runtime_safety:
        write_json(paths["runtime_safety_report"], {"status": "ok", **safe_flags()})
    return run_readiness_gate_audit(
        paper_soak_report=paths["paper_soak_report"],
        runtime_safety_report=paths["runtime_safety_report"],
        critical_alerting_report=paths["critical_alerting_report"],
        risk_recovery_report=paths["risk_recovery_report"],
        market_health_report=paths["market_health_report"],
        state_reconciliation_report=paths["state_reconciliation_report"],
        ledger_report=paths["ledger_report"],
        data_quality_report=paths["data_quality_report"],
        anti_leakage_report=paths["anti_leakage_report"],
        monte_carlo_report=paths["monte_carlo_report"],
        monte_carlo_risk_budget_policy_report=paths["monte_carlo_risk_budget_policy_report"],
        event_backtest_report=paths["event_backtest_report"],
        report_path=paths["readiness_gate_report"],
        required_soak_days=required_days,
        strict=strict,
        now=NOW,
    )


def test_soak_report_handles_missing_sources(tmp_path: Path) -> None:
    report_path = tmp_path / "reports" / "paper_soak_report.json"
    report = build_paper_shadow_soak_report(
        financial_event_log=tmp_path / "missing.jsonl",
        critical_alerting_report=tmp_path / "missing_critical.json",
        risk_recovery_report=tmp_path / "missing_risk.json",
        market_health_report=tmp_path / "missing_market.json",
        state_reconciliation_report=tmp_path / "missing_state.json",
        ledger_report=tmp_path / "missing_ledger.json",
        anti_leakage_report=tmp_path / "missing_leakage.json",
        monte_carlo_report=tmp_path / "missing_monte.json",
        event_backtest_report=tmp_path / "missing_backtest.json",
        data_quality_report=tmp_path / "missing_quality.json",
        report_path=report_path,
        now=NOW,
    )
    assert report["status"] == "missing_data"
    assert "financial_event_log" in report["missing_sources"]
    assert report_path.exists()


def test_soak_report_calculates_soak_days_and_remaining_days(tmp_path: Path) -> None:
    report = build_soak(tmp_path, required_days=3)
    assert report["soak_days"] == 2.0
    assert report["observed_soak_days"] == 2.0
    assert report["observed_soak_hours"] == 48.0
    assert report["remaining_soak_days"] == 1.0
    assert report["remaining_soak_hours"] == 24.0
    assert report["paper_events_count"] == 1
    assert report["shadow_events_count"] == 1


def test_paper_soak_reports_remaining_soak_days(tmp_path: Path) -> None:
    report = build_soak(tmp_path, required_days=7, strict=True)

    assert report["status"] == "insufficient_soak"
    assert report["observed_soak_days"] == 2.0
    assert report["required_soak_days"] == 7
    assert report["remaining_soak_days"] == 5.0
    assert "continue_soak_until_required_days" in report["next_required_actions"]


def test_paper_soak_groups_blockers_by_category(tmp_path: Path) -> None:
    paths = write_soak_sources(
        tmp_path,
        overrides={
            "financial_threshold_report": {"paper_pnl_net": -10.0, "expectancy": -0.1, "profit_factor": 0.8, "sample_warning": True},
            "monte_carlo_report": {"status": "blocked", "reason": "risk_budget_exceeded"},
        },
    )
    write_json(
        paths["monte_carlo_risk_budget_policy_report"],
        {
            "status": "blocked",
            "policy_action": "no_trade",
            "readiness_may_proceed": False,
            "live_release_allowed": False,
            **safe_flags(),
        },
    )
    report = build_paper_shadow_soak_report(
        financial_event_log=paths["financial_event_log"],
        critical_alerting_report=paths["critical_alerting_report"],
        risk_recovery_report=paths["risk_recovery_report"],
        market_health_report=paths["market_health_report"],
        state_reconciliation_report=paths["state_reconciliation_report"],
        ledger_report=paths["ledger_report"],
        ai_governance_report=paths["ai_governance_report"],
        risk_readiness_report=paths["risk_readiness_report"],
        drift_report=paths["drift_report"],
        financial_threshold_report=paths["financial_threshold_report"],
        anti_leakage_report=paths["anti_leakage_report"],
        monte_carlo_report=paths["monte_carlo_report"],
        monte_carlo_risk_budget_policy_report=paths["monte_carlo_risk_budget_policy_report"],
        event_backtest_report=paths["event_backtest_report"],
        data_quality_report=paths["data_quality_report"],
        dataset_manifest=paths["dataset_manifest"],
        report_path=paths["paper_soak_report"],
        required_soak_days=7,
        strict=True,
        now=NOW,
    )

    grouped = report["blocking_reasons_by_category"]
    assert "monte_carlo_no_trade_policy_active" in grouped["risk_policy"]
    assert "soak_days_below_required" in grouped["soak_duration"]
    assert "financial_expectancy_negative" in grouped["financial_performance"]
    assert report["performance_summary"]["paper_pnl_negative"] is True
    assert "improve_expectancy_profit_factor_and_risk_of_ruin" in report["next_required_actions"]


def test_paper_soak_keeps_no_trade_policy_blocked(tmp_path: Path) -> None:
    paths = write_soak_sources(
        tmp_path,
        overrides={"monte_carlo_report": {"status": "blocked", "reason": "risk_budget_exceeded"}},
    )
    write_json(
        paths["monte_carlo_risk_budget_policy_report"],
        {
            "status": "blocked",
            "policy_action": "no_trade",
            "readiness_may_proceed": False,
            "live_release_allowed": False,
            **safe_flags(),
        },
    )

    report = build_paper_shadow_soak_report(
        financial_event_log=paths["financial_event_log"],
        critical_alerting_report=paths["critical_alerting_report"],
        risk_recovery_report=paths["risk_recovery_report"],
        market_health_report=paths["market_health_report"],
        state_reconciliation_report=paths["state_reconciliation_report"],
        ledger_report=paths["ledger_report"],
        anti_leakage_report=paths["anti_leakage_report"],
        monte_carlo_report=paths["monte_carlo_report"],
        monte_carlo_risk_budget_policy_report=paths["monte_carlo_risk_budget_policy_report"],
        event_backtest_report=paths["event_backtest_report"],
        data_quality_report=paths["data_quality_report"],
        report_path=paths["paper_soak_report"],
        required_soak_days=2,
        now=NOW,
    )

    assert report["status"] == "blocked"
    assert report["no_trade_policy_present"] is True
    assert report["readiness_may_proceed"] is False
    assert report["readiness_approved"] is False
    assert report["live_release_allowed"] is False
    assert "monte_carlo_no_trade_policy_active" in report["readiness_blockers"]


def test_soak_report_blocks_insufficient_soak_in_strict_mode(tmp_path: Path) -> None:
    report = build_soak(tmp_path, required_days=7, strict=True)
    assert report["status"] == "insufficient_soak"
    assert "soak_days_below_required" in report["readiness_blockers"]


def test_soak_report_blocks_p0_p1_incidents(tmp_path: Path) -> None:
    report = build_soak(tmp_path, overrides={"risk_readiness_report": {"p0_incidents": 1, "p1_incidents": 1}})
    assert report["status"] == "blocked"
    assert "p0_incidents_gt_0" in report["readiness_blockers"]
    assert "p1_incidents_gt_0" in report["readiness_blockers"]


def test_soak_report_blocks_duplicate_orders(tmp_path: Path) -> None:
    report = build_soak(tmp_path, overrides={"ledger_report": {"duplicate_idempotency_key_count": 1, "duplicate_client_order_id_count": 1}})
    assert report["status"] == "blocked"
    assert "duplicate_order_intents_gt_0" in report["readiness_blockers"]
    assert "duplicate_client_order_id_gt_0" in report["readiness_blockers"]


def test_soak_report_blocks_dispatch_unknown(tmp_path: Path) -> None:
    report = build_soak(tmp_path, overrides={"ledger_report": {"dispatch_unknown_count": 1}})
    assert report["status"] == "blocked"
    assert "dispatch_unknown_gt_0" in report["readiness_blockers"]


def test_soak_report_blocks_reconciliation_required(tmp_path: Path) -> None:
    report = build_soak(tmp_path, overrides={"state_reconciliation_report": {"reconciliation_required": True}})
    assert report["status"] == "blocked"
    assert "reconciliation_required_gt_0" in report["readiness_blockers"]


def test_soak_report_blocks_market_health_blocked(tmp_path: Path) -> None:
    report = build_soak(tmp_path, overrides={"market_health_report": {"status": "blocked", "stale_data_count": 1}})
    assert report["status"] == "blocked"
    assert "market_data_health_blocked" in report["readiness_blockers"]
    assert "stale_data_count_gt_0" in report["readiness_blockers"]


def test_soak_report_blocks_risk_panic_or_reconciling(tmp_path: Path) -> None:
    report = build_soak(tmp_path, overrides={"risk_recovery_report": {"recommended_mode": "PANIC"}})
    assert report["status"] == "blocked"
    assert "risk_recovery_mode_panic" in report["readiness_blockers"]


def test_soak_report_blocks_unsafe_safety_flags(tmp_path: Path) -> None:
    paths = write_soak_sources(tmp_path, overrides={"critical_alerting_report": {"live_trading_enabled": True}})
    report = build_paper_shadow_soak_report(
        financial_event_log=paths["financial_event_log"],
        critical_alerting_report=paths["critical_alerting_report"],
        risk_recovery_report=paths["risk_recovery_report"],
        market_health_report=paths["market_health_report"],
        state_reconciliation_report=paths["state_reconciliation_report"],
        ledger_report=paths["ledger_report"],
        anti_leakage_report=paths["anti_leakage_report"],
        monte_carlo_report=paths["monte_carlo_report"],
        event_backtest_report=paths["event_backtest_report"],
        data_quality_report=paths["data_quality_report"],
        report_path=paths["paper_soak_report"],
        required_soak_days=2,
        now=NOW,
    )
    assert report["status"] == "blocked"
    assert "unsafe_source_safety_flag:critical_alerting_report:live_trading_enabled" in report["readiness_blockers"]


def test_readiness_gate_approves_only_when_all_gates_ok(tmp_path: Path) -> None:
    build_soak(tmp_path, required_days=2)
    report = run_gate(tmp_path, required_days=2)
    assert report["status"] == "ok"
    assert report["readiness_approved"] is True
    assert report["live_trading_enabled"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False
    assert report["exchange_private_access"] is False


def test_readiness_gate_blocks_when_any_critical_gate_blocked(tmp_path: Path) -> None:
    build_soak(tmp_path, overrides={"market_health_report": {"status": "blocked"}}, required_days=2)
    report = run_gate(tmp_path, required_days=2)
    assert report["status"] == "blocked"
    assert "market_health_report" in report["blocking_gates"]


def test_readiness_gate_blocks_missing_required_evidence_in_strict_mode(tmp_path: Path) -> None:
    build_soak(tmp_path, required_days=2)
    report = run_gate(tmp_path, strict=True, required_days=2, omit_runtime_safety=True)
    assert report["status"] == "blocked"
    assert "runtime_safety_report" in report["missing_gates"]
    assert "missing_required_evidence:runtime_safety_report" in report["readiness_blockers"]


def test_readiness_gate_distinguishes_policy_block_from_technical_failure(tmp_path: Path) -> None:
    paths = write_soak_sources(
        tmp_path,
        overrides={"monte_carlo_report": {"status": "blocked", "reason": "risk_budget_exceeded"}},
    )
    write_json(
        paths["monte_carlo_risk_budget_policy_report"],
        {
            "status": "blocked",
            "policy_action": "no_trade",
            "readiness_may_proceed": False,
            "live_release_allowed": False,
            **safe_flags(),
        },
    )
    soak = build_paper_shadow_soak_report(
        financial_event_log=paths["financial_event_log"],
        critical_alerting_report=paths["critical_alerting_report"],
        risk_recovery_report=paths["risk_recovery_report"],
        market_health_report=paths["market_health_report"],
        state_reconciliation_report=paths["state_reconciliation_report"],
        ledger_report=paths["ledger_report"],
        anti_leakage_report=paths["anti_leakage_report"],
        monte_carlo_report=paths["monte_carlo_report"],
        monte_carlo_risk_budget_policy_report=paths["monte_carlo_risk_budget_policy_report"],
        event_backtest_report=paths["event_backtest_report"],
        data_quality_report=paths["data_quality_report"],
        report_path=paths["paper_soak_report"],
        required_soak_days=2,
        now=NOW,
    )
    write_json(paths["runtime_safety_report"], {"status": "ok", **safe_flags()})

    report = run_readiness_gate_audit(
        paper_soak_report=paths["paper_soak_report"],
        runtime_safety_report=paths["runtime_safety_report"],
        critical_alerting_report=paths["critical_alerting_report"],
        risk_recovery_report=paths["risk_recovery_report"],
        market_health_report=paths["market_health_report"],
        state_reconciliation_report=paths["state_reconciliation_report"],
        ledger_report=paths["ledger_report"],
        data_quality_report=paths["data_quality_report"],
        anti_leakage_report=paths["anti_leakage_report"],
        monte_carlo_report=paths["monte_carlo_report"],
        monte_carlo_risk_budget_policy_report=paths["monte_carlo_risk_budget_policy_report"],
        event_backtest_report=paths["event_backtest_report"],
        report_path=paths["readiness_gate_report"],
        required_soak_days=2,
        now=NOW,
    )

    assert soak["status"] == "blocked"
    assert report["status"] == "blocked"
    assert report["blocked_by_policy"] is True
    assert report["blocked_by_technical_failure"] is False
    assert "no_trade_policy_active" in report["readiness_blockers"]


def test_readiness_gate_exposes_no_trade_exit_requirements(tmp_path: Path) -> None:
    build_soak(tmp_path, required_days=7, strict=True)
    report = run_gate(tmp_path, required_days=7)

    assert report["blocked_by_soak_duration"] is True
    assert report["remaining_soak_days"] == 5.0
    assert "expectancy_non_negative_or_positive" in report["no_trade_exit_requirements"]
    assert "keep_live_disabled" in report["next_required_actions"]


def test_readiness_gate_never_enables_live(tmp_path: Path) -> None:
    build_soak(tmp_path, required_days=2)
    report = run_gate(tmp_path, required_days=2)
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["live_trading_enabled"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False
    assert report["exchange_private_access"] is False


def test_cli_build_paper_shadow_soak_report_runs_successfully(tmp_path: Path, capsys) -> None:
    paths = write_soak_sources(tmp_path)
    rc = soak_cli.main(
        [
            "--financial-event-log",
            str(paths["financial_event_log"]),
            "--critical-alerting-report",
            str(paths["critical_alerting_report"]),
            "--risk-recovery-report",
            str(paths["risk_recovery_report"]),
            "--market-health-report",
            str(paths["market_health_report"]),
            "--state-reconciliation-report",
            str(paths["state_reconciliation_report"]),
            "--ledger-report",
            str(paths["ledger_report"]),
            "--ai-governance-report",
            str(paths["ai_governance_report"]),
            "--risk-readiness-report",
            str(paths["risk_readiness_report"]),
            "--drift-report",
            str(paths["drift_report"]),
            "--financial-threshold-report",
            str(paths["financial_threshold_report"]),
            "--anti-leakage-report",
            str(paths["anti_leakage_report"]),
            "--monte-carlo-report",
            str(paths["monte_carlo_report"]),
            "--event-backtest-report",
            str(paths["event_backtest_report"]),
            "--data-quality-report",
            str(paths["data_quality_report"]),
            "--dataset-manifest",
            str(paths["dataset_manifest"]),
            "--report",
            str(paths["paper_soak_report"]),
            "--required-soak-days",
            "1",
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert output["status"] in {"ok", "warning"}
    assert output["paper_only"] is True
    assert paths["paper_soak_report"].exists()


def test_cli_run_readiness_gate_audit_runs_successfully(tmp_path: Path, capsys) -> None:
    build_soak(tmp_path, required_days=2)
    paths = source_paths(tmp_path)
    write_json(paths["runtime_safety_report"], {"status": "ok", **safe_flags()})
    rc = gate_cli.main(
        [
            "--paper-soak-report",
            str(paths["paper_soak_report"]),
            "--runtime-safety-report",
            str(paths["runtime_safety_report"]),
            "--critical-alerting-report",
            str(paths["critical_alerting_report"]),
            "--risk-recovery-report",
            str(paths["risk_recovery_report"]),
            "--market-health-report",
            str(paths["market_health_report"]),
            "--state-reconciliation-report",
            str(paths["state_reconciliation_report"]),
            "--ledger-report",
            str(paths["ledger_report"]),
            "--data-quality-report",
            str(paths["data_quality_report"]),
            "--anti-leakage-report",
            str(paths["anti_leakage_report"]),
            "--monte-carlo-report",
            str(paths["monte_carlo_report"]),
            "--event-backtest-report",
            str(paths["event_backtest_report"]),
            "--report",
            str(paths["readiness_gate_report"]),
            "--required-soak-days",
            "2",
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert output["status"] == "ok"
    assert output["readiness_approved"] is True


def test_does_not_touch_training_dataset_or_trades_master(tmp_path: Path) -> None:
    protected = {
        "training_dataset": tmp_path / "data" / "features" / "training_dataset.parquet",
        "trades_master": tmp_path / "data" / "trades" / "trades_master.xlsx",
    }
    for path in protected.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("sentinel", encoding="utf-8")
    build_soak(tmp_path, required_days=2)
    run_gate(tmp_path, required_days=2)
    assert {name: path.read_text(encoding="utf-8") for name, path in protected.items()} == {
        "training_dataset": "sentinel",
        "trades_master": "sentinel",
    }


def test_does_not_touch_freqtrade_db_registry_models_signal_producer_or_config(tmp_path: Path) -> None:
    protected = [
        tmp_path / "freqtrade" / "user_data" / "tradesv3.paper.sqlite",
        tmp_path / "data" / "models" / "registry" / "model_registry.json",
        tmp_path / "data" / "models" / "shadow" / "model.joblib",
        tmp_path / "data" / "runtime" / "active_freqtrade_signals.json",
        tmp_path / ".env",
    ]
    for path in protected:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("sentinel", encoding="utf-8")
    build_soak(tmp_path, required_days=2)
    run_gate(tmp_path, required_days=2)
    assert all(path.read_text(encoding="utf-8") == "sentinel" for path in protected)


def test_never_sends_orders_or_accesses_exchange() -> None:
    checked_files = [
        Path("smartcrypto/ops/paper_shadow_soak_report.py"),
        Path("smartcrypto/ops/readiness_gate.py"),
        Path("scripts/build_paper_shadow_soak_report.py"),
        Path("scripts/run_readiness_gate_audit.py"),
    ]
    combined = "\n".join(path.read_text(encoding="utf-8").lower() for path in checked_files)
    forbidden = ["create_order", "fetch_balance", "private_get", "freqtradeapi", "ccxt.", "requests.post"]
    assert not any(token in combined for token in forbidden)
