from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from smartcrypto.dashboard.risk_readiness_soak_panel import (
    RUNTIME_MODE_LABELS,
    load_risk_readiness_soak_state,
)


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def soak_payload(**overrides):
    payload = {
        "status": "ok",
        "runtime_mode": "paper",
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "paper_days": 9,
        "required_paper_days": 7,
        "clean_streak_days": 8,
        "duplicate_orders_count": 0,
        "unknown_state_count": 0,
        "divergence_count": 0,
        "stale_data_count": 0,
        "shadow_order_attempts": 0,
        "controlled_live_attempts": 0,
    }
    payload.update(overrides)
    return payload


def session_payload(**overrides):
    payload = {
        "status": "ok",
        "backup_status": "pass",
        "restore_status": "pass",
        "offsite_status": "pass",
        "external_copy_status": "pass",
        "open_incidents": 0,
        "p0_incidents": 0,
        "p1_incidents": 0,
    }
    payload.update(overrides)
    return payload


def all_source_paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "paper_soak_report": tmp_path / "paper_soak.json",
        "paper_session_report": tmp_path / "paper_session.json",
        "ai_governance_report": tmp_path / "ai_governance.json",
        "data_quality_report": tmp_path / "data_quality.json",
        "dataset_manifest": tmp_path / "dataset_manifest.json",
        "anti_leakage_report": tmp_path / "anti_leakage.json",
        "monte_carlo_report": tmp_path / "monte_carlo.json",
        "monte_carlo_risk_budget_policy_report": tmp_path / "monte_carlo_policy.json",
        "backtest_report": tmp_path / "backtest.json",
        "kill_switch": tmp_path / "kill_switch.json",
        "active_signals": tmp_path / "active_signals.json",
        "signal_decisions": tmp_path / "signal_decisions.jsonl",
    }


def write_all_sources(tmp_path: Path, **overrides) -> dict[str, Path]:
    paths = all_source_paths(tmp_path)
    write_json(paths["paper_soak_report"], overrides.get("paper_soak_report", soak_payload()))
    write_json(paths["paper_session_report"], overrides.get("paper_session_report", session_payload()))
    write_json(paths["ai_governance_report"], overrides.get("ai_governance_report", {"status": "ok", "paper_only": True, "shadow_only": True}))
    write_json(paths["data_quality_report"], overrides.get("data_quality_report", {"status": "ok"}))
    write_json(paths["dataset_manifest"], overrides.get("dataset_manifest", {"status": "ok"}))
    write_json(paths["anti_leakage_report"], overrides.get("anti_leakage_report", {"status": "ok"}))
    write_json(paths["monte_carlo_report"], overrides.get("monte_carlo_report", {"status": "ok", "recommendation_status": "ok"}))
    write_json(
        paths["monte_carlo_risk_budget_policy_report"],
        overrides.get(
            "monte_carlo_risk_budget_policy_report",
            {
                "status": "policy_present",
                "policy_action": "observe_only",
                "readiness_may_proceed": True,
                "live_release_allowed": False,
                "paper_only": True,
                "shadow_only": True,
                "live_trading_enabled": False,
                "order_submission_enabled": False,
                "real_order_submission_enabled": False,
                "exchange_private_access": False,
            },
        ),
    )
    write_json(paths["backtest_report"], overrides.get("backtest_report", {"status": "ok"}))
    write_json(paths["kill_switch"], overrides.get("kill_switch", {"enabled": False, "status": "inactive", "reason": "clear"}))
    write_json(paths["active_signals"], overrides.get("active_signals", {"generated_at_utc": "2026-06-03T11:58:00Z", "signals": []}))
    write_jsonl(
        paths["signal_decisions"],
        overrides.get(
            "signal_decisions",
            [
                {"created_at": "2026-06-03T11:58:30Z", "decision": "SHADOW_SKIP"},
                {"created_at": "2026-06-03T11:59:00Z", "decision": "SHADOW_ENTRY"},
            ],
        ),
    )
    return paths


def load(paths: dict[str, Path], **kwargs):
    return load_risk_readiness_soak_state(source_paths=paths, now=NOW, **kwargs)


def test_risk_readiness_panel_handles_missing_sources(tmp_path):
    state = load(all_source_paths(tmp_path))

    assert state["status"] == "missing_data"
    assert "paper_soak_report" in state["missing_sources"]
    assert state["is_read_only"] is True


def test_risk_readiness_panel_reports_runtime_modes(tmp_path):
    paths = write_all_sources(tmp_path)

    state = load(paths)

    assert state["status"] == "ok"
    assert "PAPER" in state["runtime_modes"]
    assert "SHADOW" in state["runtime_modes"]
    assert "LIVE_LOCKED" in state["runtime_modes"]
    assert all(mode in RUNTIME_MODE_LABELS for mode in state["runtime_modes"])


def test_risk_readiness_panel_reports_paper_shadow_soak_progress(tmp_path):
    paths = write_all_sources(tmp_path, paper_soak_report=soak_payload(paper_days=5, clean_streak_days=4))

    state = load(paths, required_paper_days=7)

    assert state["status"] == "blocked"
    assert state["paper_days"] == 5
    assert state["paper_days_observed"] == 5
    assert state["required_paper_days"] == 7
    assert state["paper_days_required"] == 7
    assert state["remaining_paper_days"] == 2
    assert state["paper_days_remaining"] == 2
    assert "paper_days_below_required" in state["readiness_blockers"]


def test_risk_readiness_panel_blocks_live_flags(tmp_path):
    paths = write_all_sources(tmp_path, paper_soak_report=soak_payload(live_trading_enabled=True, exchange_private_access=True))

    state = load(paths)

    assert state["status"] == "blocked"
    assert "live_trading_enabled_true" in state["readiness_blockers"]
    assert "exchange_private_access_true" in state["readiness_blockers"]


def test_risk_readiness_panel_blocks_order_submission_flags(tmp_path):
    paths = write_all_sources(
        tmp_path,
        paper_soak_report=soak_payload(order_submission_enabled=True, real_order_submission_enabled=True),
    )

    state = load(paths)

    assert state["status"] == "blocked"
    assert "order_submission_enabled_true" in state["readiness_blockers"]
    assert "real_order_submission_enabled_true" in state["readiness_blockers"]


def test_risk_readiness_panel_blocks_shadow_or_live_attempts(tmp_path):
    paths = write_all_sources(tmp_path, paper_soak_report=soak_payload(shadow_order_attempts=1, controlled_live_attempts=1))

    state = load(paths)

    assert state["status"] == "blocked"
    assert "shadow_order_attempts_gt_0" in state["readiness_blockers"]
    assert "controlled_live_attempts_gt_0" in state["readiness_blockers"]


def test_risk_readiness_panel_blocks_duplicates_unknowns_and_divergences(tmp_path):
    paths = write_all_sources(
        tmp_path,
        paper_soak_report=soak_payload(duplicate_orders_count=1, unknown_state_count=2, divergence_count=3),
    )

    state = load(paths)

    assert state["status"] == "blocked"
    assert "duplicate_orders_count_gt_0" in state["readiness_blockers"]
    assert "unknown_state_count_gt_0" in state["readiness_blockers"]
    assert "divergence_count_gt_0" in state["readiness_blockers"]
    assert "RECONCILING" in state["runtime_modes"]


def test_risk_readiness_panel_blocks_p0_p1_incidents(tmp_path):
    paths = write_all_sources(tmp_path, paper_session_report=session_payload(open_incidents=2, p0_incidents=1, p1_incidents=1))

    state = load(paths)

    assert state["status"] == "blocked"
    assert state["open_incidents"] == 2
    assert "p0_incidents_gt_0" in state["readiness_blockers"]
    assert "p1_incidents_gt_0" in state["readiness_blockers"]


def test_risk_readiness_panel_reports_kill_switch_status(tmp_path):
    paths = write_all_sources(tmp_path, kill_switch={"enabled": True})

    state = load(paths)

    assert state["status"] == "blocked"
    assert state["kill_switch_status"] == "active_unclear"
    assert "kill_switch_active_without_clear_classification" in state["readiness_blockers"]
    assert "PANIC" in state["runtime_modes"]


def test_risk_readiness_panel_reports_backup_restore_offsite_status(tmp_path):
    paths = write_all_sources(
        tmp_path,
        paper_session_report=session_payload(backup_status="warning", restore_status="pass", offsite_status="missing", external_copy_status="pass"),
    )

    state = load(paths)

    assert state["status"] == "blocked"
    assert state["backup_status"] == "warning"
    assert state["restore_status"] == "pass"
    assert state["offsite_status"] == "missing"
    assert state["external_copy_status"] == "pass"
    assert "backup_status_not_pass" in state["readiness_blockers"]


def test_risk_readiness_panel_reports_stale_signal_warning(tmp_path):
    paths = write_all_sources(tmp_path, active_signals={"generated_at_utc": "2026-06-03T10:00:00Z"})

    state = load(paths, max_stale_signal_age_seconds=900)

    assert state["status"] == "blocked"
    assert state["latest_signal_age_seconds"] == 7200
    assert "stale_data_count_above_limit" in state["readiness_blockers"]
    assert "STALE_DATA" in state["runtime_modes"]


def test_risk_readiness_exposes_no_trade_policy_fields(tmp_path):
    paths = write_all_sources(
        tmp_path,
        monte_carlo_report={"status": "blocked", "reason": "risk_budget_exceeded"},
        monte_carlo_risk_budget_policy_report={
            "status": "blocked",
            "policy_action": "no_trade",
            "readiness_may_proceed": False,
            "live_release_allowed": False,
            "paper_only": True,
            "shadow_only": True,
            "live_trading_enabled": False,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "exchange_private_access": False,
        },
    )

    state = load(paths)

    assert state["monte_carlo_risk_treated"] is True
    assert state["no_trade_policy_present"] is True
    assert state["monte_carlo_risk_budget_policy_action"] == "no_trade"
    assert state["readiness_may_proceed"] is False
    assert state["live_release_allowed"] is False
    assert "monte_carlo_policy_action_not_no_trade" in state["no_trade_exit_requirements"]
    assert "collect_financial_outcomes_for_expectancy_profit_factor_risk_of_ruin" in state["next_collection_targets"]
    assert "artifact_status_blocked:monte_carlo_report" not in state["readiness_blockers"]
    assert "artifact_status_blocked:monte_carlo_risk_budget_policy_report" not in state["readiness_blockers"]


def test_risk_readiness_dashboard_exposes_collection_targets(tmp_path):
    paths = write_all_sources(
        tmp_path,
        paper_soak_report=soak_payload(paper_days=3, clean_streak_days=3),
        monte_carlo_report={"status": "blocked", "reason": "risk_budget_exceeded"},
        monte_carlo_risk_budget_policy_report={
            "status": "blocked",
            "policy_action": "no_trade",
            "readiness_may_proceed": False,
            "live_release_allowed": False,
            "paper_only": True,
            "shadow_only": True,
            "live_trading_enabled": False,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "exchange_private_access": False,
        },
    )

    state = load(paths, required_paper_days=7)

    assert state["status"] == "blocked"
    assert state["paper_days_remaining"] == 4
    assert "collect_paper_shadow_soak_days:4" in state["next_collection_targets"]
    assert "collect_financial_outcomes_for_expectancy_profit_factor_risk_of_ruin" in state["next_collection_targets"]
    assert "risk_of_ruin_within_policy_cap" in state["no_trade_exit_requirements"]
    assert state["live_release_allowed"] is False


def test_risk_readiness_keeps_blocked_for_no_trade_policy_and_soak_days(tmp_path):
    paths = write_all_sources(
        tmp_path,
        paper_soak_report=soak_payload(
            status="blocked",
            paper_days=2,
            clean_streak_days=2,
            readiness_blockers=["monte_carlo_no_trade_policy_active", "soak_days_below_required"],
        ),
        monte_carlo_report={"status": "blocked", "reason": "risk_budget_exceeded"},
        monte_carlo_risk_budget_policy_report={
            "status": "blocked",
            "policy_action": "no_trade",
            "readiness_may_proceed": False,
            "live_release_allowed": False,
            "paper_only": True,
            "shadow_only": True,
            "live_trading_enabled": False,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "exchange_private_access": False,
        },
    )

    state = load(paths, required_paper_days=7)

    assert state["status"] == "blocked"
    assert "monte_carlo_no_trade_policy_active" in state["readiness_blockers"]
    assert "paper_days_below_required" in state["readiness_blockers"]
    assert "artifact_status_blocked:paper_soak_report" not in state["readiness_blockers"]


def test_risk_readiness_reports_stale_source_details(tmp_path):
    paths = write_all_sources(tmp_path, active_signals={"generated_at_utc": "2026-06-03T10:00:00Z", "signals": []})

    state = load(paths, max_stale_signal_age_seconds=900)

    assert "stale_data_count_above_limit" in state["readiness_blockers"]
    assert state["stale_sources"] == ["active_signals"]
    assert state["stale_source_details"][0]["source"] == "active_signals"
    assert state["stale_source_details"][0]["timestamp_utc"] == "2026-06-03T10:00:00Z"
    assert state["stale_source_details"][0]["age_seconds"] == 7200
    assert state["stale_source_details"][0]["limit_seconds"] == 900


def test_risk_readiness_panel_never_marks_ok_when_critical_gate_blocked(tmp_path):
    paths = write_all_sources(tmp_path, data_quality_report={"status": "blocked"})

    state = load(paths)

    assert state["status"] == "blocked"
    assert state["readiness_approved"] is False
    assert "artifact_status_blocked:data_quality_report" in state["readiness_blockers"]


def test_risk_readiness_panel_is_read_only(tmp_path):
    paths = write_all_sources(tmp_path)

    state = load(paths)

    assert state["read_only"] is True
    assert state["forbidden_actions_present"] == []
    assert state["readiness_approved"] is True


def test_reports_never_allow_live_release(tmp_path):
    paths = write_all_sources(tmp_path)
    state = load(paths)

    assert state["live_release_allowed"] is False
    assert state["live_trading_enabled"] is False
    assert state["order_submission_enabled"] is False
    assert state["real_order_submission_enabled"] is False


def test_reports_never_send_orders_or_access_exchange(tmp_path):
    paths = write_all_sources(tmp_path)
    state = load(paths)

    assert state["exchange_private_access"] is False
    assert state["safety_flags"]["sends_orders"] is False
    assert state["safety_flags"]["changes_risk"] is False
    assert state["forbidden_actions_present"] == []


def test_risk_readiness_panel_has_no_live_order_or_risk_actions():
    text = Path("smartcrypto/dashboard/risk_readiness_soak_panel.py").read_text(encoding="utf-8")
    forbidden = [
        "st.button",
        "set_kill_switch",
        "create_order",
        "cancel_order",
        "fetch_balance",
        "to_parquet",
        "to_csv",
        "write_registry",
    ]

    assert all(token not in text for token in forbidden)


def test_dashboard_module_does_not_import_ccxt_or_exchange_clients():
    text = Path("smartcrypto/dashboard/risk_readiness_soak_panel.py").read_text(encoding="utf-8")
    forbidden = ["ccxt", "binance", "private_get", "create_order", "cancel_order", "fetch_balance"]

    assert all(token not in text for token in forbidden)


def test_dashboard_does_not_touch_runtime_registry_models_signal_producer_or_freqtrade(tmp_path):
    sentinels = [
        tmp_path / "kill_switch.json",
        tmp_path / "model_registry.json",
        tmp_path / "shadow_model.pkl",
        tmp_path / "active_freqtrade_signals.json",
        tmp_path / "tradesv3.paper.sqlite",
    ]
    for sentinel in sentinels:
        sentinel.write_text(f"sentinel:{sentinel.name}", encoding="utf-8")
    before = {path: path.read_text(encoding="utf-8") for path in sentinels}
    paths = write_all_sources(tmp_path / "sources")

    load(paths)

    assert {path: path.read_text(encoding="utf-8") for path in sentinels} == before


def test_cli_inspect_risk_readiness_sources_runs_successfully(tmp_path):
    paths = write_all_sources(tmp_path / "sources")
    report_path = tmp_path / "risk_readiness_report.json"

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "inspect_risk_readiness_soak_sources.py"),
            "--paper-soak-report",
            str(paths["paper_soak_report"]),
            "--paper-session-report",
            str(paths["paper_session_report"]),
            "--ai-governance-report",
            str(paths["ai_governance_report"]),
            "--data-quality-report",
            str(paths["data_quality_report"]),
            "--dataset-manifest",
            str(paths["dataset_manifest"]),
            "--anti-leakage-report",
            str(paths["anti_leakage_report"]),
            "--monte-carlo-report",
            str(paths["monte_carlo_report"]),
            "--monte-carlo-risk-budget-policy-report",
            str(paths["monte_carlo_risk_budget_policy_report"]),
            "--backtest-report",
            str(paths["backtest_report"]),
            "--kill-switch",
            str(paths["kill_switch"]),
            "--active-signals",
            str(paths["active_signals"]),
            "--signal-decisions",
            str(paths["signal_decisions"]),
            "--report",
            str(report_path),
            "--required-paper-days",
            "7",
            "--max-stale-signal-age-seconds",
            "999999",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["status"] in {"ok", "warning"}
    assert payload["paper_days"] == 9
    assert report_path.exists()
