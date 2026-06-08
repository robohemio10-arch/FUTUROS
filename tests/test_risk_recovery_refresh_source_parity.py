from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from scripts.refresh_runtime_evidence_reports import refresh_runtime_evidence_reports


NOW = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)


def safe_flags() -> dict[str, object]:
    return {
        "paper_only": True,
        "shadow_only": True,
        "runtime_mode": "paper",
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
    }


def write_json(path: Path, payload: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return path


def write_frame(path: Path, rows: list[dict[str, object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows)
    if path.suffix.lower() == ".parquet":
        frame.to_parquet(path, index=False)
    else:
        frame.to_csv(path, index=False)
    return path


def iso(days_ago: int = 0) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def write_runtime_inputs(tmp_path: Path, *, blocked_readiness: bool = False) -> dict[str, Path]:
    reports = tmp_path / "reports"
    runtime = tmp_path / "runtime"
    dockerfile = tmp_path / "Dockerfile"
    compose = tmp_path / "docker-compose.paper.yml"
    dockerfile.write_text(
        "FROM python:3.12\nHEALTHCHECK CMD python -m smartcrypto.runtime.container_healthcheck --quiet\n",
        encoding="utf-8",
    )
    compose.write_text("services:\n  app:\n    image: smartcrypto\n", encoding="utf-8")
    readiness_blockers = ["no_trade_policy_active", "soak_days_below_required"] if blocked_readiness else []
    soak_blockers = ["monte_carlo_no_trade_policy_active", "soak_days_below_required"] if blocked_readiness else []
    paths = {
        "refresh": reports / "runtime_evidence_refresh_report.json",
        "readiness": reports / "readiness_gate_report.json",
        "soak": reports / "paper_soak_report.json",
        "critical_event_log": runtime / "financial_event_log.jsonl",
        "critical": reports / "critical_alerting_report.json",
        "risk": reports / "risk_recovery_mode_audit_report.json",
        "market": reports / "market_data_health_audit_report.json",
        "state_reconciliation": reports / "state_reconciliation_audit_report.json",
        "ledger": reports / "order_intent_capital_ledger_audit_report.json",
        "backup": reports / "backup_snapshot_report.json",
        "restore": reports / "restore_dry_run_report.json",
        "system": reports / "system_healthcheck_report.json",
        "equity": reports / "equity_curve.parquet",
        "closed": reports / "closed_trades.csv",
        "paper_session": reports / "paper_session_report.json",
        "monte": reports / "monte_carlo_risk_simulation_report.json",
        "backtest": reports / "event_driven_backtest_report.json",
        "kill": runtime / "kill_switch.json",
        "incidents": reports / "incidents_report.json",
        "state_divergence": reports / "risk_state_divergence.json",
        "dockerfile": dockerfile,
        "compose": compose,
    }
    write_json(
        paths["readiness"],
        {
            "status": "blocked" if blocked_readiness else "ok",
            "readiness_approved": not blocked_readiness,
            "readiness_blockers": readiness_blockers,
            "no_trade_policy_present": blocked_readiness,
            "generated_at_utc": iso(),
            **safe_flags(),
        },
    )
    write_json(
        paths["soak"],
        {
            "status": "blocked" if blocked_readiness else "ok",
            "readiness_blockers": soak_blockers,
            "no_trade_policy_present": blocked_readiness,
            "generated_at_utc": iso(),
            **safe_flags(),
        },
    )
    write_json(paths["critical"], {"status": "ok", "critical_alerts": 0, "generated_at_utc": iso(), **safe_flags()})
    write_json(paths["risk"], {"status": "ok", "recommended_mode": "NORMAL", "generated_at_utc": iso(), **safe_flags()})
    write_json(paths["market"], {"status": "ok", "stale_data_count": 0, "generated_at_utc": iso(), **safe_flags()})
    write_json(paths["state_reconciliation"], {"status": "ok", "reconciliation_required": False, "generated_at_utc": iso(), **safe_flags()})
    write_json(paths["ledger"], {"status": "ok", "generated_at_utc": iso(), **safe_flags()})
    write_json(paths["backup"], {"status": "ok", "generated_at_utc": iso(), **safe_flags()})
    write_json(paths["restore"], {"status": "ok", "generated_at_utc": iso(), **safe_flags()})
    write_frame(
        paths["equity"],
        [
            {"timestamp": iso(3), "equity": 1000.0},
            {"timestamp": iso(1), "equity": 1005.0},
            {"timestamp": iso(0), "equity": 1010.0},
        ],
    )
    write_frame(
        paths["closed"],
        [
            {"closed_at": iso(2), "pnl": 2.5},
            {"closed_at": iso(1), "pnl": -1.0},
            {"closed_at": iso(0), "pnl": 3.0},
        ],
    )
    write_json(
        paths["paper_session"],
        {
            "status": "ok",
            "backup_status": "pass",
            "restore_status": "pass",
            "clean_streak_days": 4,
            "recovery_approved": False,
            "p0_incidents": 0,
            "p1_incidents": 0,
            "generated_at_utc": iso(),
            **safe_flags(),
        },
    )
    write_json(paths["monte"], {"status": "ok", "generated_at_utc": iso(), **safe_flags()})
    write_json(paths["backtest"], {"status": "ok", "generated_at_utc": iso(), **safe_flags()})
    write_json(paths["kill"], {"enabled": False, "status": "inactive", "generated_at_utc": iso(), **safe_flags()})
    write_json(paths["incidents"], {"p0": 0, "p1": 0, "open": 0, "generated_at_utc": iso(), **safe_flags()})
    write_json(paths["state_divergence"], {"reconciliation_required": False, "divergence_count": 0, "generated_at_utc": iso(), **safe_flags()})
    return paths


def run_refresh(paths: dict[str, Path], **overrides: object) -> dict[str, object]:
    kwargs = {
        "report_path": paths["refresh"],
        "readiness_report": paths["readiness"],
        "paper_soak_report": paths["soak"],
        "critical_event_log": paths["critical_event_log"],
        "critical_alerting_report": paths["critical"],
        "risk_recovery_report": paths["risk"],
        "equity_curve": paths["equity"],
        "closed_trades": paths["closed"],
        "paper_session_report": paths["paper_session"],
        "monte_carlo_report": paths["monte"],
        "backtest_report": paths["backtest"],
        "kill_switch": paths["kill"],
        "incidents": paths["incidents"],
        "state_divergence_report": paths["state_divergence"],
        "market_health_report": paths["market"],
        "skip_market_health": True,
        "state_repository": None,
        "state_reconciliation_report": paths["state_reconciliation"],
        "ledger_repository": None,
        "ledger_report": paths["ledger"],
        "backup_report": paths["backup"],
        "restore_report": paths["restore"],
        "system_healthcheck_report": paths["system"],
        "dockerfile": paths["dockerfile"],
        "compose_file": paths["compose"],
        "now": NOW,
    }
    kwargs.update(overrides)
    return refresh_runtime_evidence_reports(**kwargs)


def load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_refresh_passes_complete_risk_recovery_sources_when_provided(tmp_path: Path) -> None:
    paths = write_runtime_inputs(tmp_path)

    report = run_refresh(paths)
    risk = load(paths["risk"])

    assert report["risk_recovery_sources_passed"]["closed_trades"] == str(paths["closed"])
    assert report["risk_recovery_sources_passed"]["paper_session_report"] == str(paths["paper_session"])
    assert report["risk_recovery_sources_passed"]["state_divergence_report"] == str(paths["state_divergence"])
    assert report["risk_recovery_optional_sources_missing"] == []
    assert set(report["risk_recovery_source_status"]) >= {
        "equity_curve",
        "closed_trades",
        "paper_session_report",
        "market_health_report",
        "readiness_report",
        "monte_carlo_report",
        "backtest_report",
        "kill_switch",
        "incidents",
        "state_divergence_report",
    }
    assert risk["status"] == "ok"
    assert risk["evidence_quality_summary"]["primary_state"] == "recovery_mode_inactive"


def test_risk_recovery_does_not_regress_to_missing_runtime_sources_when_sources_exist(tmp_path: Path) -> None:
    paths = write_runtime_inputs(tmp_path)

    report = run_refresh(paths)
    risk = load(paths["risk"])

    assert report["risk_recovery_reason"] != "missing_runtime_sources"
    assert risk["evidence_quality_summary"]["primary_state"] != "missing_runtime_sources"
    assert risk["missing_sources"] == []


def test_missing_optional_sources_are_diagnostic_not_false_ok(tmp_path: Path) -> None:
    paths = write_runtime_inputs(tmp_path)
    paths["equity"].unlink()
    paths["closed"].unlink()

    report = run_refresh(paths)
    risk = load(paths["risk"])

    assert set(report["risk_recovery_optional_sources_missing"]) >= {"equity_curve", "closed_trades"}
    assert risk["status"] != "ok"
    assert risk["evidence_quality_summary"]["operational_evidence_complete"] is False
    assert risk["evidence_quality_summary"]["primary_state"] == "market_health_ok_but_no_recovery_state"


def test_healthcheck_remains_blocked_only_by_paper_soak_and_readiness_when_reports_are_fresh(tmp_path: Path) -> None:
    paths = write_runtime_inputs(tmp_path, blocked_readiness=True)

    report = run_refresh(paths)

    blockers = set(report["system_health_blocking_findings"])
    assert report["system_health_status"] == "blocked"
    assert {"readiness_gate_blocked", "no_trade_policy_active", "soak_days_below_required"} <= blockers
    assert "risk_recovery_mode_panic" not in blockers
    assert "risk_recovery_mode_reconciling" not in blockers
    assert "state_reconciliation_blocked" not in blockers
    assert "ledger_audit_blocked" not in blockers
    assert report["live_release_allowed"] is False
    assert report["readiness_approved"] is False


def test_refresh_preserves_paper_shadow_only_safety_flags(tmp_path: Path) -> None:
    paths = write_runtime_inputs(tmp_path)

    report = run_refresh(paths)
    risk = load(paths["risk"])

    for payload in (report, risk):
        assert payload["paper_only"] is True
        assert payload["shadow_only"] is True
        assert payload["live_trading_enabled"] is False
        assert payload["order_submission_enabled"] is False
        assert payload["real_order_submission_enabled"] is False
        assert payload["sends_orders"] is False
        assert payload["exchange_private_access"] is False
