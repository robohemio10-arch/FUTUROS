from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scripts import build_final_technical_audit_report as audit_cli
from smartcrypto.ops.final_technical_audit import REPORT_FILES, build_final_technical_audit_report

NOW = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)


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
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows), encoding="utf-8")
    return path


def clean_payloads() -> dict[str, dict]:
    flags = safe_flags()
    return {
        "readiness_gate": {"status": "ok", "readiness_approved": True, **flags},
        "paper_soak": {"status": "ok", "soak_days": 8, "readiness_blockers": [], **flags},
        "system_healthcheck": {"status": "ok", "blocking_findings": [], **flags},
        "backup_snapshot": {"status": "ok", "file_count": 2, **flags},
        "restore_dry_run": {"status": "ok", "dry_run": True, **flags},
        "sklearn_compatibility": {"status": "ok", "blocking_findings": [], **flags},
        "runtime_safety": {"status": "ok", "blocking_findings": [], **flags},
        "critical_alerting": {"status": "ok", "critical_alerts": 0, **flags},
        "risk_recovery": {"status": "ok", "recommended_mode": "NORMAL", **flags},
        "market_data_health": {"status": "ok", "stale_data_count": 0, **flags},
        "state_reconciliation": {"status": "ok", "reconciliation_required": False, **flags},
        "ledger": {"status": "ok", "duplicate_client_order_id_count": 0, **flags},
        "ai_governance": {"status": "ok", **flags},
        "risk_readiness_soak_dashboard": {"status": "ok", **flags},
        "drift_monitor": {"status": "ok", **flags},
        "financial_thresholds": {"status": "ok", "paper_pnl_net": 10, **flags},
        "anti_leakage": {"status": "ok", **flags},
        "monte_carlo": {"status": "ok", **flags},
        "event_backtest": {"status": "ok", **flags},
        "data_quality": {"status": "ok", **flags},
        "dataset_manifest": {"status": "ok", "rows": 2864, **flags},
        "model_registry_gate": {"status": "ok", "promotion_allowed": False, **flags},
        "ai_shadow_trainer": {"status": "ok", "auto_promote": False, **flags},
    }


def write_reports(root: Path, *, overrides: dict[str, dict] | None = None, financial_events: list[dict] | None = None) -> Path:
    payloads = clean_payloads()
    for name, patch in (overrides or {}).items():
        payloads[name] = {**payloads[name], **patch}
    for name, filename in REPORT_FILES.items():
        path = root / filename
        if name == "financial_event_log":
            write_jsonl(path, financial_events or [{"event_type": "audit", "severity": "INFO"}])
        else:
            write_json(path, payloads[name])
    return root


def run_audit(root: Path, *, overrides: dict[str, dict] | None = None, strict: bool = False, financial_events: list[dict] | None = None) -> dict:
    write_reports(root, overrides=overrides, financial_events=financial_events)
    return build_final_technical_audit_report(
        reports_root=root,
        output_path=root / "final_technical_audit_20_pillars_report.json",
        project_root=root.parent,
        strict=strict,
        now=NOW,
    )


def test_final_audit_handles_missing_sources(tmp_path: Path) -> None:
    report = build_final_technical_audit_report(reports_root=tmp_path / "reports", output_path=tmp_path / "report.json", project_root=tmp_path, now=NOW)
    assert report["status"] == "warning"
    assert report["missing_evidence"]
    assert len(report["pillars"]) == 20


def test_final_audit_scores_all_20_pillars(tmp_path: Path) -> None:
    report = run_audit(tmp_path / "reports")
    assert len(report["pillars"]) == 20
    assert {pillar["id"] for pillar in report["pillars"]} == set(range(1, 21))
    assert report["overall_score"] > 0
    assert all("current_score" in pillar for pillar in report["pillars"])


def test_final_audit_blocks_when_p0_exists(tmp_path: Path) -> None:
    report = run_audit(tmp_path / "reports", overrides={"critical_alerting": {"p0_findings": ["exchange_private_access_attempt"]}})
    assert report["status"] == "blocked"
    assert "p0_findings_present" in report["global_blockers"]


def test_final_audit_blocks_when_p1_live_blocking_exists(tmp_path: Path) -> None:
    report = run_audit(tmp_path / "reports", overrides={"readiness_gate": {"p1_findings": ["live_blocking:paper_soak_incomplete"]}})
    assert report["status"] == "blocked"
    assert "p1_live_blocking_findings_present" in report["global_blockers"]


def test_final_audit_blocks_unsafe_safety_flags(tmp_path: Path) -> None:
    report = run_audit(tmp_path / "reports", overrides={"runtime_safety": {"live_trading_enabled": True}})
    assert report["status"] == "blocked"
    assert "unsafe_source_safety_flag:runtime_safety:live_trading_enabled" in report["global_blockers"]


def test_final_audit_blocks_when_readiness_gate_blocked(tmp_path: Path) -> None:
    report = run_audit(tmp_path / "reports", overrides={"readiness_gate": {"status": "blocked", "readiness_approved": False}})
    assert report["status"] == "blocked"
    assert "readiness_gate_blocked" in report["global_blockers"]


def test_final_audit_blocks_when_market_data_health_blocked(tmp_path: Path) -> None:
    report = run_audit(tmp_path / "reports", overrides={"market_data_health": {"status": "blocked"}})
    assert report["status"] == "blocked"
    assert "market_data_health_blocked" in report["global_blockers"]


def test_final_audit_blocks_when_state_reconciliation_blocked(tmp_path: Path) -> None:
    report = run_audit(tmp_path / "reports", overrides={"state_reconciliation": {"status": "ok", "reconciliation_required": True}})
    assert report["status"] == "blocked"
    assert "state_reconciliation_blocked" in report["global_blockers"]


def test_final_audit_blocks_when_ledger_audit_blocked(tmp_path: Path) -> None:
    report = run_audit(tmp_path / "reports", overrides={"ledger": {"status": "blocked"}})
    assert report["status"] == "blocked"
    assert "ledger_audit_blocked" in report["global_blockers"]


def test_final_audit_blocks_when_anti_leakage_blocked(tmp_path: Path) -> None:
    report = run_audit(tmp_path / "reports", overrides={"anti_leakage": {"status": "blocked"}})
    assert report["status"] == "blocked"
    assert "anti_leakage_blocked" in report["global_blockers"]


def test_final_audit_blocks_when_monte_carlo_blocked(tmp_path: Path) -> None:
    report = run_audit(tmp_path / "reports", overrides={"monte_carlo": {"status": "blocked"}})
    assert report["status"] == "blocked"
    assert "monte_carlo_blocked" in report["global_blockers"]


def test_final_audit_blocks_when_event_backtest_blocked(tmp_path: Path) -> None:
    report = run_audit(tmp_path / "reports", overrides={"event_backtest": {"status": "blocked"}})
    assert report["status"] == "blocked"
    assert "event_backtest_blocked" in report["global_blockers"]


def test_final_audit_blocks_when_sklearn_compatibility_blocked(tmp_path: Path) -> None:
    report = run_audit(tmp_path / "reports", overrides={"sklearn_compatibility": {"status": "blocked"}})
    assert report["status"] == "blocked"
    assert "sklearn_compatibility_blocked" in report["global_blockers"]


def test_final_audit_never_allows_live_release(tmp_path: Path) -> None:
    report = run_audit(tmp_path / "reports")
    assert report["live_release_allowed"] is False
    assert report["paper_shadow_only"] is True


def test_final_audit_requires_manual_go_no_go(tmp_path: Path) -> None:
    report = run_audit(tmp_path / "reports")
    assert report["manual_go_no_go_required"] is True


def test_final_audit_reports_missing_evidence(tmp_path: Path) -> None:
    root = write_reports(tmp_path / "reports")
    (root / REPORT_FILES["monte_carlo"]).unlink()
    report = build_final_technical_audit_report(reports_root=root, output_path=root / "report.json", project_root=tmp_path, now=NOW)
    assert "monte_carlo" in report["missing_evidence"]
    assert report["evidence_summary"]["missing_count"] >= 1


def test_final_audit_generates_next_required_actions(tmp_path: Path) -> None:
    report = run_audit(tmp_path / "reports", overrides={"sklearn_compatibility": {"status": "blocked"}})
    assert "keep_live_disabled" in report["next_required_actions"]
    assert "align_sklearn_model_runtime_versions" in report["next_required_actions"]


def test_cli_build_final_technical_audit_report_runs_successfully(tmp_path: Path, capsys) -> None:
    root = write_reports(tmp_path / "reports")
    rc = audit_cli.main(["--reports-root", str(root), "--output", str(tmp_path / "audit.json"), "--project-root", str(tmp_path)])
    output = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert len(output["pillars"]) == 20
    assert output["live_release_allowed"] is False


def test_does_not_touch_training_dataset_or_trades_master(tmp_path: Path) -> None:
    protected = [
        tmp_path / "data" / "features" / "training_dataset.parquet",
        tmp_path / "data" / "trades" / "trades_master.xlsx",
    ]
    for path in protected:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("sentinel", encoding="utf-8")
    run_audit(tmp_path / "reports")
    assert all(path.read_text(encoding="utf-8") == "sentinel" for path in protected)


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
    build_final_technical_audit_report(reports_root=tmp_path / "reports", output_path=tmp_path / "audit.json", project_root=tmp_path, now=NOW)
    assert all(path.read_text(encoding="utf-8") == "sentinel" for path in protected)


def test_never_sends_orders_or_accesses_exchange() -> None:
    checked = [
        Path("smartcrypto/ops/final_technical_audit.py"),
        Path("scripts/build_final_technical_audit_report.py"),
    ]
    combined = "\n".join(path.read_text(encoding="utf-8").lower() for path in checked)
    forbidden = ["create_order", "fetch_balance", "private_get", "freqtradeapi", "ccxt.", "requests.post"]
    assert not any(token in combined for token in forbidden)
