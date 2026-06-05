from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.dashboard.ai_governance_panel import (
    FORBIDDEN_ACTION_LABELS,
    load_ai_governance_panel_state,
)


ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def registry_payload(**overrides):
    payload = {
        "registry_version": 2,
        "updated_at_utc": "2026-06-03T00:00:00Z",
        "champion_model_id": "champion-1",
        "champion_model_version": "v1",
        "challengers": [
            {
                "model_id": "challenger-1",
                "model_version": "v2",
                "promotion_status": "pending",
            }
        ],
        "rejected_promotions": [],
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
    }
    payload.update(overrides)
    return payload


def trainer_payload(**overrides):
    payload = {
        "status": "ok",
        "model_id": "challenger-1",
        "model_version": "v2",
        "input_rows": 26,
        "feature_columns": ["feature_close", "feature_volume"],
        "target_column": "target_profitable",
        "class_balance": {"0": 12, "1": 14},
        "metrics": {"accuracy": 0.62, "precision": 0.6, "recall": 0.7, "f1": 0.65},
        "sample_warning": True,
        "promotion_status": "pending",
        "auto_promote": False,
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
    }
    payload.update(overrides)
    return payload


def gate_payload(**overrides):
    payload = {
        "status": "ok",
        "promotion_status": "pending",
        "promotion_allowed": False,
        "auto_promote": False,
        "promotion_violations": ["sample_warning"],
        "rejection_reasons": ["insufficient_sample"],
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
    }
    payload.update(overrides)
    return payload


def decision_payload(**overrides):
    payload = {
        "created_at": "2026-06-03T00:00:00Z",
        "model_id": "challenger-1",
        "symbol": "BTCUSDT",
        "decision": "AI_ACCEPT",
        "probability_win": 0.61,
        "shadow_only": True,
    }
    payload.update(overrides)
    return payload


def all_source_paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "registry": tmp_path / "model_registry.json",
        "trainer_report": tmp_path / "trainer.json",
        "promotion_report": tmp_path / "promotion.json",
        "drift_report": tmp_path / "drift.json",
        "outcomes_report": tmp_path / "outcomes.json",
        "financial_report": tmp_path / "financial.json",
        "anti_leakage_report": tmp_path / "anti_leakage.json",
        "monte_carlo_report": tmp_path / "monte_carlo.json",
        "monte_carlo_risk_budget_policy_report": tmp_path / "monte_carlo_policy.json",
        "backtest_report": tmp_path / "backtest.json",
        "data_quality_report": tmp_path / "data_quality.json",
        "dataset_manifest": tmp_path / "manifest.json",
        "decisions_jsonl": tmp_path / "decisions.jsonl",
    }


def write_all_sources(tmp_path: Path, **overrides) -> dict[str, Path]:
    paths = all_source_paths(tmp_path)
    write_json(paths["registry"], overrides.get("registry", registry_payload()))
    write_json(paths["trainer_report"], overrides.get("trainer_report", trainer_payload()))
    write_json(paths["promotion_report"], overrides.get("promotion_report", gate_payload()))
    write_json(paths["drift_report"], overrides.get("drift_report", {"status": "ok", "drift_status": "ok"}))
    write_json(paths["outcomes_report"], overrides.get("outcomes_report", {"status": "ok", "outcome_tracking_status": "ok"}))
    write_json(paths["financial_report"], overrides.get("financial_report", {"status": "ok", "recommendation": "keep_threshold"}))
    write_json(paths["anti_leakage_report"], overrides.get("anti_leakage_report", {"status": "ok"}))
    write_json(paths["monte_carlo_report"], overrides.get("monte_carlo_report", {"status": "ok", "recommendation_status": "ok"}))
    write_json(
        paths["monte_carlo_risk_budget_policy_report"],
        overrides.get(
            "monte_carlo_risk_budget_policy_report",
            {"status": "ok", "policy_action": "observe_only", "live_release_allowed": False, **registry_payload()},
        ),
    )
    write_json(paths["backtest_report"], overrides.get("backtest_report", {"status": "ok"}))
    write_json(paths["data_quality_report"], overrides.get("data_quality_report", {"status": "ok"}))
    write_json(paths["dataset_manifest"], overrides.get("dataset_manifest", {"status": "ok"}))
    write_jsonl(
        paths["decisions_jsonl"],
        overrides.get(
            "decisions",
            [
                decision_payload(decision="AI_REJECT", probability_win=0.42),
                decision_payload(decision="AI_ACCEPT", probability_win=0.61),
            ],
        ),
    )
    return paths


def test_ai_governance_panel_handles_missing_sources(tmp_path):
    state = load_ai_governance_panel_state(source_paths=all_source_paths(tmp_path))

    assert state["status"] == "missing_data"
    assert "registry" in state["missing_sources"]
    assert state["is_read_only"] is True


def test_ai_governance_panel_reads_model_registry(tmp_path):
    paths = write_all_sources(tmp_path)

    state = load_ai_governance_panel_state(source_paths=paths)

    assert state["registry"]["registry_version"] == 2
    assert state["status"] == "warning"


def test_ai_governance_panel_reports_champion_and_challengers(tmp_path):
    paths = write_all_sources(tmp_path)

    state = load_ai_governance_panel_state(source_paths=paths)

    assert state["champion_model_id"] == "champion-1"
    assert state["champion_model_version"] == "v1"
    assert state["challengers"][0]["model_id"] == "challenger-1"


def test_ai_governance_panel_reports_promotion_gate_blocks(tmp_path):
    paths = write_all_sources(tmp_path)

    state = load_ai_governance_panel_state(source_paths=paths)

    assert state["blocked_gates"] == ["sample_warning"]
    assert state["rejection_reasons"] == ["insufficient_sample"]
    assert state["promotion_status"] == "pending"


def test_ai_governance_panel_reports_trainer_metrics(tmp_path):
    paths = write_all_sources(tmp_path)

    state = load_ai_governance_panel_state(source_paths=paths)

    assert state["trainer_metrics"]["f1"] == 0.65
    assert state["sample_warning"] is True


def test_ai_governance_panel_reports_drift_status(tmp_path):
    paths = write_all_sources(tmp_path, drift_report={"status": "ok", "drift_status": "blocked"})

    state = load_ai_governance_panel_state(source_paths=paths)

    assert state["status"] == "blocked"
    assert state["drift_status"] == "blocked"
    assert "drift_status_blocked" in state["blocked_reasons"]


def test_ai_governance_panel_reports_financial_thresholds(tmp_path):
    paths = write_all_sources(
        tmp_path,
        financial_report={"status": "ok", "recommended_threshold": 0.63},
    )

    state = load_ai_governance_panel_state(source_paths=paths)

    assert state["financial_threshold_recommendation"] == 0.63


def test_ai_governance_does_not_block_ok_drift_or_financial_thresholds(tmp_path):
    paths = write_all_sources(
        tmp_path,
        drift_report={"status": "ok", "drift_status": "ok"},
        financial_report={"status": "ok", "recommended_threshold": 0.7},
    )

    state = load_ai_governance_panel_state(source_paths=paths)

    assert "drift_status_blocked" not in state["blocked_reasons"]
    assert "artifact_status_blocked:drift_report" not in state["blocked_reasons"]
    assert "artifact_status_blocked:financial_report" not in state["blocked_reasons"]
    assert state["drift_status"] == "ok"
    assert state["financial_threshold_recommendation"] == 0.7


def test_ai_governance_reclassifies_monte_carlo_no_trade_policy_as_treated(tmp_path):
    paths = write_all_sources(
        tmp_path,
        monte_carlo_report={"status": "blocked", "reason": "risk_of_ruin_above_limit"},
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
            "sends_orders": False,
            "changes_risk": False,
        },
    )

    state = load_ai_governance_panel_state(source_paths=paths)

    assert state["monte_carlo_risk_treated"] is True
    assert state["no_trade_policy_present"] is True
    assert state["monte_carlo_risk_budget_policy_action"] == "no_trade"
    assert "artifact_status_blocked:monte_carlo_report" not in state["blocked_reasons"]
    assert "artifact_status_blocked:monte_carlo_risk_budget_policy_report" not in state["blocked_reasons"]
    assert state["live_release_allowed"] is False


def test_ai_governance_panel_reports_latest_shadow_decision(tmp_path):
    paths = write_all_sources(tmp_path)

    state = load_ai_governance_panel_state(source_paths=paths)

    assert state["latest_shadow_decision"]["decision"] == "AI_ACCEPT"
    assert state["latest_shadow_decision"]["probability_win"] == 0.61


def test_ai_governance_panel_reports_outcomes(tmp_path):
    paths = write_all_sources(tmp_path, outcomes_report={"status": "ok", "outcome_tracking_status": "synced"})

    state = load_ai_governance_panel_state(source_paths=paths)

    assert state["latest_outcome_tracking_status"] == "synced"


def test_ai_governance_panel_blocks_unsafe_safety_flags(tmp_path):
    paths = write_all_sources(
        tmp_path,
        trainer_report=trainer_payload(live_trading_enabled=True, order_submission_enabled=True),
    )

    state = load_ai_governance_panel_state(source_paths=paths)

    assert state["status"] == "blocked"
    assert "unsafe_safety_flag:live_trading_enabled_true" in state["blocked_reasons"]
    assert "unsafe_safety_flag:order_submission_enabled_true" in state["blocked_reasons"]


def test_ai_governance_panel_is_read_only(tmp_path):
    paths = write_all_sources(tmp_path)

    state = load_ai_governance_panel_state(source_paths=paths)

    assert state["read_only"] is True
    assert state["forbidden_actions_present"] == []
    assert state["safety_flags"]["sends_orders"] is False
    assert state["safety_flags"]["changes_risk"] is False


def test_ai_governance_panel_has_no_promote_or_order_actions():
    text = Path("smartcrypto/dashboard/ai_governance_panel.py").read_text(encoding="utf-8").lower()

    assert "st.button" not in text
    assert "create_order" not in text
    assert "send_order" not in text
    assert all(label in FORBIDDEN_ACTION_LABELS for label in FORBIDDEN_ACTION_LABELS)


def test_dashboard_module_does_not_import_ccxt_or_exchange_clients():
    text = Path("smartcrypto/dashboard/ai_governance_panel.py").read_text(encoding="utf-8")
    forbidden = ["ccxt", "binance", "fetch_balance", "private_get", "create_order", "cancel_order"]

    assert all(token not in text for token in forbidden)


def test_dashboard_does_not_touch_registry_models_signal_producer_or_freqtrade(tmp_path):
    sentinels = [
        tmp_path / "model_registry.json",
        tmp_path / "shadow_model.pkl",
        tmp_path / "active_freqtrade_signals.json",
        tmp_path / "tradesv3.paper.sqlite",
    ]
    for sentinel in sentinels:
        sentinel.write_text(f"sentinel:{sentinel.name}", encoding="utf-8")
    before = {path: path.read_text(encoding="utf-8") for path in sentinels}
    paths = write_all_sources(tmp_path / "sources")

    load_ai_governance_panel_state(source_paths=paths)

    assert {path: path.read_text(encoding="utf-8") for path in sentinels} == before


def test_cli_inspect_ai_governance_sources_runs_successfully(tmp_path):
    paths = write_all_sources(tmp_path / "sources")
    report_path = tmp_path / "ai_governance_report.json"

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "inspect_ai_governance_dashboard_sources.py"),
            "--registry",
            str(paths["registry"]),
            "--trainer-report",
            str(paths["trainer_report"]),
            "--promotion-report",
            str(paths["promotion_report"]),
            "--drift-report",
            str(paths["drift_report"]),
            "--outcomes-report",
            str(paths["outcomes_report"]),
            "--financial-report",
            str(paths["financial_report"]),
            "--anti-leakage-report",
            str(paths["anti_leakage_report"]),
            "--monte-carlo-report",
            str(paths["monte_carlo_report"]),
            "--monte-carlo-risk-budget-policy-report",
            str(paths["monte_carlo_risk_budget_policy_report"]),
            "--backtest-report",
            str(paths["backtest_report"]),
            "--data-quality-report",
            str(paths["data_quality_report"]),
            "--dataset-manifest",
            str(paths["dataset_manifest"]),
            "--decisions-jsonl",
            str(paths["decisions_jsonl"]),
            "--report",
            str(report_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["champion_model_id"] == "champion-1"
    assert report_path.exists()
