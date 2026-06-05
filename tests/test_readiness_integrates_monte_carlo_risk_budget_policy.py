from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scripts import build_final_technical_audit_report as final_cli
from scripts import build_paper_shadow_soak_report as soak_cli
from scripts import inspect_risk_readiness_soak_sources as dashboard_cli
from scripts import run_readiness_gate_audit as readiness_cli
from smartcrypto.dashboard.risk_readiness_soak_panel import load_risk_readiness_soak_state
from smartcrypto.ops.final_technical_audit import REPORT_FILES, build_final_technical_audit_report
from smartcrypto.ops.paper_shadow_soak_report import build_paper_shadow_soak_report
from smartcrypto.ops.readiness_gate import run_readiness_gate_audit

NOW = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)


def safe_flags() -> dict[str, object]:
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


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return path


def write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows), encoding="utf-8")
    return path


def blocked_monte_carlo() -> dict:
    return {
        "status": "blocked",
        "reason": "risk_of_ruin_exceeds_limit",
        "risk_metrics": {
            "risk_of_ruin": 0.911,
            "expectancy_per_trade": -36.0179,
            "simulated_profit_factor": 0.6901,
            "p95_max_drawdown_pct": 728.46,
        },
        **safe_flags(),
    }


def valid_no_trade_policy(**overrides) -> dict:
    payload = {
        "status": "blocked",
        "risk_budget_status": "blocked",
        "policy_action": "no_trade",
        "no_trade_reason": [
            "expectancy_negative",
            "p95_drawdown_exceeds_cap",
            "profit_factor_below_minimum",
            "risk_of_ruin_exceeds_cap",
        ],
        "max_stake_recommended": 0,
        "max_leverage_recommended": 0,
        "readiness_may_proceed": False,
        "live_release_allowed": False,
        **safe_flags(),
    }
    payload.update(overrides)
    return payload


def reports(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "reports"
    return {
        "financial_event_log": root / "financial_event_log.jsonl",
        "paper_soak_report": root / "paper_soak_report.json",
        "runtime_safety_report": root / "runtime_safety_config_validation_report.json",
        "critical_alerting_report": root / "critical_alerting_report.json",
        "risk_recovery_report": root / "risk_recovery_mode_audit_report.json",
        "market_health_report": root / "market_data_health_audit_report.json",
        "state_reconciliation_report": root / "state_reconciliation_audit_report.json",
        "ledger_report": root / "order_intent_capital_ledger_audit_report.json",
        "ai_governance_report": root / "ai_governance_dashboard_sources_report.json",
        "risk_readiness_report": root / "risk_readiness_soak_dashboard_sources_report.json",
        "drift_report": root / "ai_shadow_drift_monitor_report.json",
        "financial_threshold_report": root / "ai_shadow_financial_threshold_evaluation_report.json",
        "anti_leakage_report": root / "phase23_anti_leakage_report.json",
        "monte_carlo_report": root / "monte_carlo_risk_simulation_report.json",
        "monte_carlo_policy_report": root / "monte_carlo_risk_budget_policy_report.json",
        "event_backtest_report": root / "event_driven_backtest_report.json",
        "data_quality_report": root / "data_quality_report.json",
        "dataset_manifest": root / "dataset_manifest.json",
        "paper_session_report": root / "paper_session_report.json",
        "ai_governance": root / "ai_governance_dashboard_sources_report.json",
        "backtest_report": root / "event_driven_backtest_report.json",
        "kill_switch": tmp_path / "runtime" / "kill_switch.json",
        "active_signals": tmp_path / "runtime" / "active_freqtrade_signals.json",
        "signal_decisions": tmp_path / "runtime" / "freqtrade_signal_decisions.jsonl",
        "readiness_gate_report": root / "readiness_gate_report.json",
        "dashboard_report": root / "risk_readiness_soak_dashboard_sources_report.json",
    }


def write_common_sources(tmp_path: Path, *, policy: dict | None = None, monte_carlo: dict | None = None) -> dict[str, Path]:
    paths = reports(tmp_path)
    flags = safe_flags()
    write_jsonl(paths["financial_event_log"], [{"event_type": "signal", "occurred_at_utc": "2026-06-03T12:00:00Z", "runtime_mode": "paper"}])
    write_json(paths["critical_alerting_report"], {"status": "ok", "critical_alerts": 0, **flags})
    write_json(paths["risk_recovery_report"], {"status": "ok", "recommended_mode": "NORMAL", "blocking_findings": [], **flags})
    write_json(paths["market_health_report"], {"status": "ok", "stale_data_count": 0, **flags})
    write_json(paths["state_reconciliation_report"], {"status": "ok", "reconciliation_required": False, "state_divergence_count": 0, **flags})
    write_json(paths["ledger_report"], {"status": "ok", "order_intents_count": 1, "duplicate_idempotency_key_count": 0, "duplicate_client_order_id_count": 0, "dispatch_unknown_count": 0, **flags})
    write_json(paths["ai_governance_report"], {"status": "ok", **flags})
    write_json(paths["risk_readiness_report"], {"status": "ok", "clean_streak_days": 8, "p0_incidents": 0, "p1_incidents": 0, **flags})
    write_json(paths["drift_report"], {"status": "ok", "drift_blocks": 0, **flags})
    write_json(paths["financial_threshold_report"], {"status": "ok", **flags})
    write_json(paths["anti_leakage_report"], {"status": "ok", **flags})
    write_json(paths["monte_carlo_report"], monte_carlo or blocked_monte_carlo())
    if policy is not None:
        write_json(paths["monte_carlo_policy_report"], policy)
    write_json(paths["event_backtest_report"], {"status": "ok", **flags})
    write_json(paths["data_quality_report"], {"status": "ok", **flags})
    write_json(paths["dataset_manifest"], {"status": "ok", "rows": 2973, **flags})
    write_json(paths["runtime_safety_report"], {"status": "ok", **flags})
    write_json(paths["paper_soak_report"], {"status": "ok", "soak_days": 9, "paper_days": 9, "readiness_blockers": [], **flags})
    write_json(paths["paper_session_report"], {"status": "ok", "backup_status": "pass", "restore_status": "pass", "offsite_status": "pass", "external_copy_status": "pass", "p0_incidents": 0, "p1_incidents": 0})
    write_json(paths["kill_switch"], {"enabled": False, "status": "inactive"})
    write_json(paths["active_signals"], {"generated_at_utc": "2026-06-05T11:59:00Z", "signals": []})
    write_jsonl(paths["signal_decisions"], [{"created_at": "2026-06-05T11:59:00Z", "decision": "SHADOW_SKIP"}])
    return paths


def run_readiness(paths: dict[str, Path], *, policy: Path | None) -> dict:
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
        monte_carlo_risk_budget_policy_report=policy,
        event_backtest_report=paths["event_backtest_report"],
        report_path=paths["readiness_gate_report"],
        required_soak_days=7,
        now=NOW,
    )


def test_readiness_accepts_valid_no_trade_policy_as_risk_treatment(tmp_path: Path) -> None:
    paths = write_common_sources(tmp_path, policy=valid_no_trade_policy())
    report = run_readiness(paths, policy=paths["monte_carlo_policy_report"])

    assert report["status"] == "blocked"
    assert report["monte_carlo_risk_treated"] is True
    assert report["no_trade_policy_present"] is True
    assert report["gates"]["monte_carlo_report"]["reason"] == "monte_carlo_no_trade_policy_active"


def test_readiness_does_not_approve_when_no_trade_policy_active(tmp_path: Path) -> None:
    paths = write_common_sources(tmp_path, policy=valid_no_trade_policy())
    report = run_readiness(paths, policy=paths["monte_carlo_policy_report"])

    assert report["readiness_approved"] is False
    assert report["readiness_may_proceed"] is False
    assert "no_trade_policy_active" in report["readiness_blockers"]
    assert "keep_live_disabled" in report["next_required_actions"]
    assert "respect_no_trade_policy" in report["next_required_actions"]


def test_readiness_blocks_unsafe_policy_report(tmp_path: Path) -> None:
    paths = write_common_sources(tmp_path, policy=valid_no_trade_policy(live_release_allowed=True))
    report = run_readiness(paths, policy=paths["monte_carlo_policy_report"])

    assert report["status"] == "blocked"
    assert report["no_trade_policy_present"] is False
    assert "unsafe_policy_report" in report["readiness_blockers"]


def test_paper_soak_records_monte_carlo_policy_active(tmp_path: Path) -> None:
    paths = write_common_sources(tmp_path, policy=valid_no_trade_policy())
    report = build_paper_shadow_soak_report(
        financial_event_log=paths["financial_event_log"],
        critical_alerting_report=paths["critical_alerting_report"],
        risk_recovery_report=paths["risk_recovery_report"],
        market_health_report=paths["market_health_report"],
        state_reconciliation_report=paths["state_reconciliation_report"],
        ledger_report=paths["ledger_report"],
        anti_leakage_report=paths["anti_leakage_report"],
        monte_carlo_report=paths["monte_carlo_report"],
        monte_carlo_risk_budget_policy_report=paths["monte_carlo_policy_report"],
        event_backtest_report=paths["event_backtest_report"],
        data_quality_report=paths["data_quality_report"],
        report_path=paths["paper_soak_report"],
        required_soak_days=7,
        now=NOW,
    )

    assert report["monte_carlo_risk_budget_policy_active"] is True
    assert report["monte_carlo_risk_treated"] is True
    assert "monte_carlo_no_trade_policy_active" in report["readiness_blockers"]


def test_final_audit_reclassifies_monte_carlo_blocked_as_no_trade_policy_active(tmp_path: Path) -> None:
    paths = write_common_sources(tmp_path, policy=valid_no_trade_policy())
    report = build_final_technical_audit_report(
        reports_root=paths["monte_carlo_report"].parent,
        output_path=tmp_path / "final.json",
        project_root=tmp_path,
        now=NOW,
    )

    assert report["status"] == "blocked"
    assert report["monte_carlo_risk_treated"] is True
    assert "monte_carlo_no_trade_policy_active" in report["global_blockers"]
    assert "monte_carlo_blocked" not in report["global_blockers"]


def test_final_audit_never_allows_live_with_policy(tmp_path: Path) -> None:
    paths = write_common_sources(tmp_path, policy=valid_no_trade_policy())
    report = build_final_technical_audit_report(reports_root=paths["monte_carlo_report"].parent, output_path=tmp_path / "final.json", project_root=tmp_path, now=NOW)

    assert report["live_release_allowed"] is False
    assert report["readiness_may_proceed"] is False


def test_dashboard_sources_include_monte_carlo_policy(tmp_path: Path) -> None:
    paths = write_common_sources(tmp_path, policy=valid_no_trade_policy())
    state = load_risk_readiness_soak_state(
        source_paths={
            "paper_soak_report": paths["paper_soak_report"],
            "paper_session_report": paths["paper_session_report"],
            "ai_governance_report": paths["ai_governance_report"],
            "data_quality_report": paths["data_quality_report"],
            "dataset_manifest": paths["dataset_manifest"],
            "anti_leakage_report": paths["anti_leakage_report"],
            "monte_carlo_report": paths["monte_carlo_report"],
            "monte_carlo_risk_budget_policy_report": paths["monte_carlo_policy_report"],
            "backtest_report": paths["backtest_report"],
            "kill_switch": paths["kill_switch"],
            "active_signals": paths["active_signals"],
            "signal_decisions": paths["signal_decisions"],
        },
        now=NOW,
    )

    assert "monte_carlo_risk_budget_policy_report" in state["sources"]
    assert state["no_trade_policy_present"] is True
    assert "monte_carlo_no_trade_policy_active" in state["readiness_blockers"]


def test_missing_policy_keeps_legacy_behavior(tmp_path: Path) -> None:
    paths = write_common_sources(tmp_path, policy=None)
    report = run_readiness(paths, policy=None)

    assert report["monte_carlo_risk_treated"] is False
    assert report["no_trade_policy_present"] is False
    assert report["gates"]["monte_carlo_report"]["reason"] == "status_blocked"


def test_invalid_policy_keeps_monte_carlo_blocked(tmp_path: Path) -> None:
    paths = write_common_sources(tmp_path, policy={"status": "blocked", "policy_action": "no_trade", "live_release_allowed": True, **safe_flags()})
    report = run_readiness(paths, policy=paths["monte_carlo_policy_report"])

    assert report["monte_carlo_risk_treated"] is False
    assert report["gates"]["monte_carlo_report"]["reason"] == "status_blocked"
    assert "unsafe_policy_report" in report["readiness_blockers"]


def test_cli_readiness_accepts_monte_carlo_policy_argument(tmp_path: Path, capsys) -> None:
    paths = write_common_sources(tmp_path, policy=valid_no_trade_policy())
    rc = readiness_cli.main(
        [
            "--paper-soak-report", str(paths["paper_soak_report"]),
            "--runtime-safety-report", str(paths["runtime_safety_report"]),
            "--critical-alerting-report", str(paths["critical_alerting_report"]),
            "--risk-recovery-report", str(paths["risk_recovery_report"]),
            "--market-health-report", str(paths["market_health_report"]),
            "--state-reconciliation-report", str(paths["state_reconciliation_report"]),
            "--ledger-report", str(paths["ledger_report"]),
            "--data-quality-report", str(paths["data_quality_report"]),
            "--anti-leakage-report", str(paths["anti_leakage_report"]),
            "--monte-carlo-report", str(paths["monte_carlo_report"]),
            "--monte-carlo-risk-budget-policy-report", str(paths["monte_carlo_policy_report"]),
            "--event-backtest-report", str(paths["event_backtest_report"]),
            "--report", str(paths["readiness_gate_report"]),
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["no_trade_policy_present"] is True


def test_cli_paper_soak_accepts_monte_carlo_policy_argument(tmp_path: Path, capsys) -> None:
    paths = write_common_sources(tmp_path, policy=valid_no_trade_policy())
    rc = soak_cli.main(
        [
            "--financial-event-log", str(paths["financial_event_log"]),
            "--critical-alerting-report", str(paths["critical_alerting_report"]),
            "--risk-recovery-report", str(paths["risk_recovery_report"]),
            "--market-health-report", str(paths["market_health_report"]),
            "--state-reconciliation-report", str(paths["state_reconciliation_report"]),
            "--ledger-report", str(paths["ledger_report"]),
            "--anti-leakage-report", str(paths["anti_leakage_report"]),
            "--monte-carlo-report", str(paths["monte_carlo_report"]),
            "--monte-carlo-risk-budget-policy-report", str(paths["monte_carlo_policy_report"]),
            "--event-backtest-report", str(paths["event_backtest_report"]),
            "--data-quality-report", str(paths["data_quality_report"]),
            "--report", str(paths["paper_soak_report"]),
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["monte_carlo_risk_budget_policy_active"] is True


def test_cli_final_audit_accepts_monte_carlo_policy_source(tmp_path: Path, capsys) -> None:
    paths = write_common_sources(tmp_path, policy=valid_no_trade_policy())
    for name, filename in REPORT_FILES.items():
        if name == "financial_event_log":
            continue
        source = paths["monte_carlo_policy_report"] if name == "monte_carlo_risk_budget_policy" else reports(tmp_path).get(f"{name}_report")
        if source is None:
            continue
    rc = final_cli.main(
        [
            "--reports-root", str(paths["monte_carlo_report"].parent),
            "--output", str(tmp_path / "final.json"),
            "--project-root", str(tmp_path),
            "--monte-carlo-risk-budget-policy-report", str(paths["monte_carlo_policy_report"]),
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["no_trade_policy_present"] is True


def test_does_not_touch_freqtrade_db_models_registry_training_dataset_or_trades_master(tmp_path: Path) -> None:
    sentinels = [
        tmp_path / "freqtrade" / "user_data" / "tradesv3.paper.sqlite",
        tmp_path / "data" / "models" / "registry" / "model_registry.json",
        tmp_path / "data" / "models" / "shadow" / "model.joblib",
        tmp_path / "data" / "features" / "training_dataset.parquet",
        tmp_path / "data" / "trades" / "trades_master.xlsx",
        tmp_path / "data" / "runtime" / "active_freqtrade_signals.json",
    ]
    for sentinel in sentinels:
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("sentinel", encoding="utf-8")
    before = {path: path.read_text(encoding="utf-8") for path in sentinels}
    paths = write_common_sources(tmp_path / "sources", policy=valid_no_trade_policy())

    run_readiness(paths, policy=paths["monte_carlo_policy_report"])
    build_paper_shadow_soak_report(
        financial_event_log=paths["financial_event_log"],
        critical_alerting_report=paths["critical_alerting_report"],
        risk_recovery_report=paths["risk_recovery_report"],
        market_health_report=paths["market_health_report"],
        state_reconciliation_report=paths["state_reconciliation_report"],
        ledger_report=paths["ledger_report"],
        anti_leakage_report=paths["anti_leakage_report"],
        monte_carlo_report=paths["monte_carlo_report"],
        monte_carlo_risk_budget_policy_report=paths["monte_carlo_policy_report"],
        event_backtest_report=paths["event_backtest_report"],
        data_quality_report=paths["data_quality_report"],
        report_path=paths["paper_soak_report"],
        now=NOW,
    )

    assert {path: path.read_text(encoding="utf-8") for path in sentinels} == before


def test_never_sends_orders_or_accesses_exchange() -> None:
    checked = [
        Path("smartcrypto/ops/readiness_gate.py"),
        Path("smartcrypto/ops/paper_shadow_soak_report.py"),
        Path("smartcrypto/dashboard/risk_readiness_soak_panel.py"),
        Path("smartcrypto/ops/final_technical_audit.py"),
    ]
    combined = "\n".join(path.read_text(encoding="utf-8").lower() for path in checked)
    forbidden = ["create_order", "fetch_balance", "private_get", "freqtradeapi", "ccxt.", "requests.post"]
    assert not any(token in combined for token in forbidden)
