from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.refresh_runtime_evidence_reports import refresh_runtime_evidence_reports
from smartcrypto.ops.system_healthcheck import run_system_healthcheck
from smartcrypto.runtime.container_healthcheck import run_container_healthcheck

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)


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


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return path


def write_sources(tmp_path: Path, *, timestamp: str = "2026-06-05T12:00:00Z", blocked_readiness: bool = False) -> dict[str, Path]:
    reports = tmp_path / "reports"
    dockerfile = tmp_path / "Dockerfile"
    compose = tmp_path / "docker-compose.paper.yml"
    dockerfile.write_text("FROM python:3.11\nHEALTHCHECK CMD python -m smartcrypto.runtime.container_healthcheck --quiet\n", encoding="utf-8")
    compose.write_text("services:\n  app:\n    image: smartcrypto\n", encoding="utf-8")
    readiness_blockers = ["no_trade_policy_active", "soak_days_below_required"] if blocked_readiness else []
    soak_blockers = ["monte_carlo_no_trade_policy_active", "soak_days_below_required"] if blocked_readiness else []
    paths = {
        "readiness": reports / "readiness_gate_report.json",
        "soak": reports / "paper_soak_report.json",
        "critical": reports / "critical_alerting_report.json",
        "risk": reports / "risk_recovery_mode_audit_report.json",
        "market": reports / "market_data_health_audit_report.json",
        "state": reports / "state_reconciliation_audit_report.json",
        "ledger": reports / "order_intent_capital_ledger_audit_report.json",
        "backup": reports / "backup_snapshot_report.json",
        "restore": reports / "restore_dry_run_report.json",
        "system": reports / "system_healthcheck_report.json",
        "refresh": reports / "runtime_evidence_refresh_report.json",
        "critical_log": tmp_path / "runtime" / "financial_event_log.jsonl",
        "ledger_repo": tmp_path / "runtime" / "missing_ledger.sqlite",
        "state_repo": tmp_path / "runtime" / "missing_state.json",
        "dockerfile": dockerfile,
        "compose": compose,
    }
    write_json(
        paths["readiness"],
        {
            "status": "blocked" if blocked_readiness else "ok",
            "readiness_approved": not blocked_readiness,
            "no_trade_policy_present": blocked_readiness,
            "readiness_blockers": readiness_blockers,
            "generated_at_utc": timestamp,
            **safe_flags(),
        },
    )
    write_json(
        paths["soak"],
        {
            "status": "blocked" if blocked_readiness else "ok",
            "no_trade_policy_present": blocked_readiness,
            "readiness_blockers": soak_blockers,
            "generated_at_utc": timestamp,
            **safe_flags(),
        },
    )
    write_json(paths["critical"], {"status": "ok", "critical_alerts": 0, "generated_at_utc": timestamp, **safe_flags()})
    write_json(paths["risk"], {"status": "ok", "recommended_mode": "NORMAL", "generated_at_utc": timestamp, **safe_flags()})
    write_json(paths["market"], {"status": "ok", "generated_at_utc": timestamp, **safe_flags()})
    write_json(paths["state"], {"status": "ok", "reconciliation_required": False, "generated_at_utc": timestamp, **safe_flags()})
    write_json(paths["ledger"], {"status": "ok", "generated_at_utc": timestamp, **safe_flags()})
    write_json(paths["backup"], {"status": "ok", "generated_at_utc": timestamp, **safe_flags()})
    write_json(paths["restore"], {"status": "ok", "generated_at_utc": timestamp, **safe_flags()})
    return paths


def run_refresh(tmp_path: Path, *, blocked_readiness: bool = False) -> dict:
    paths = write_sources(tmp_path, blocked_readiness=blocked_readiness)
    return refresh_runtime_evidence_reports(
        report_path=paths["refresh"],
        readiness_report=paths["readiness"],
        paper_soak_report=paths["soak"],
        critical_event_log=paths["critical_log"],
        critical_alerting_report=paths["critical"],
        risk_recovery_report=paths["risk"],
        market_health_report=paths["market"],
        skip_market_health=True,
        state_repository=paths["state_repo"],
        state_reconciliation_report=paths["state"],
        ledger_repository=paths["ledger_repo"],
        ledger_report=paths["ledger"],
        backup_report=paths["backup"],
        restore_report=paths["restore"],
        system_healthcheck_report=paths["system"],
        dockerfile=paths["dockerfile"],
        compose_file=paths["compose"],
        now=NOW,
    )


def test_runtime_evidence_refresh_reports_safety_flags(tmp_path: Path) -> None:
    report = run_refresh(tmp_path)

    assert report["status"] == "ok"
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["live_trading_enabled"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False
    assert report["exchange_private_access"] is False
    assert report["sends_orders"] is False


def test_runtime_evidence_refresh_does_not_send_orders_or_access_exchange() -> None:
    checked = [
        ROOT / "scripts" / "refresh_runtime_evidence_reports.py",
        ROOT / "smartcrypto" / "runtime" / "container_healthcheck.py",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8").lower() for path in checked)
    forbidden = ["create_order", "fetch_balance", "private_get", "freqtradeapi", "ccxt.", "requests.post"]
    assert not any(token in combined for token in forbidden)


def test_system_healthcheck_reports_structured_stale_details(tmp_path: Path) -> None:
    paths = write_sources(tmp_path, timestamp="2026-06-05T11:00:00Z")
    report = run_system_healthcheck(
        readiness_report=paths["readiness"],
        paper_soak_report=paths["soak"],
        critical_alerting_report=paths["critical"],
        risk_recovery_report=paths["risk"],
        market_health_report=paths["market"],
        state_reconciliation_report=paths["state"],
        ledger_report=paths["ledger"],
        backup_report=paths["backup"],
        restore_report=paths["restore"],
        dockerfile=paths["dockerfile"],
        compose_file=paths["compose"],
        report_path=paths["system"],
        max_report_age_seconds=60,
        now=NOW,
    )

    assert report["stale_reports_count"] >= 7
    first = report["stale_reports"][0]
    assert {"report_name", "path", "age_seconds", "age_minutes", "stale_limit_seconds", "timestamp_key"} <= set(first)
    assert first["timestamp_key"] == "generated_at_utc"


def test_system_healthcheck_does_not_emit_missing_docker_healthcheck_when_dockerfiles_have_healthcheck(tmp_path: Path) -> None:
    paths = write_sources(tmp_path)
    report = run_system_healthcheck(
        readiness_report=paths["readiness"],
        paper_soak_report=paths["soak"],
        critical_alerting_report=paths["critical"],
        risk_recovery_report=paths["risk"],
        market_health_report=paths["market"],
        state_reconciliation_report=paths["state"],
        ledger_report=paths["ledger"],
        backup_report=paths["backup"],
        restore_report=paths["restore"],
        dockerfile=paths["dockerfile"],
        compose_file=paths["compose"],
        report_path=paths["system"],
        now=NOW,
    )

    assert report["checks"]["docker_healthcheck"]["status"] == "ok"
    assert "missing_docker_healthcheck" not in report["warnings"]


def test_container_healthcheck_never_allows_live_or_order_submission() -> None:
    report = run_container_healthcheck(
        required_paths=[],
        env={
            "SMARTCRYPTO_RUNTIME_MODE": "paper",
            "LIVE_ENABLED": "true",
            "ORDER_SUBMISSION_ENABLED": "true",
            "REAL_ORDER_SUBMISSION_ENABLED": "true",
        },
        now=NOW,
    )

    assert report["status"] == "blocked"
    assert "unsafe_safety_flag:live_trading_enabled" in report["blocking_findings"]
    assert "unsafe_safety_flag:order_submission_enabled" in report["blocking_findings"]
    assert "unsafe_safety_flag:real_order_submission_enabled" in report["blocking_findings"]


def test_dockerfiles_define_healthcheck() -> None:
    for path in [
        ROOT / "docker" / "smartcrypto" / "Dockerfile",
        ROOT / "docker" / "dashboard" / "Dockerfile",
        ROOT / "docker" / "qlib" / "Dockerfile",
    ]:
        text = path.read_text(encoding="utf-8")
        assert "HEALTHCHECK" in text
        assert "smartcrypto.runtime.container_healthcheck" in text


def test_critical_alerting_report_generated_or_reported_as_missing_generator(tmp_path: Path) -> None:
    paths = write_sources(tmp_path)
    paths["critical"].unlink()
    report = refresh_runtime_evidence_reports(
        report_path=paths["refresh"],
        readiness_report=paths["readiness"],
        paper_soak_report=paths["soak"],
        critical_event_log=paths["critical_log"],
        critical_alerting_report=paths["critical"],
        risk_recovery_report=paths["risk"],
        market_health_report=paths["market"],
        skip_market_health=True,
        state_repository=paths["state_repo"],
        state_reconciliation_report=paths["state"],
        ledger_repository=paths["ledger_repo"],
        ledger_report=paths["ledger"],
        backup_report=paths["backup"],
        restore_report=paths["restore"],
        system_healthcheck_report=paths["system"],
        dockerfile=paths["dockerfile"],
        compose_file=paths["compose"],
        now=NOW,
    )

    assert paths["critical"].exists()
    assert "critical_alerting_report" in report["refreshed_reports"]
    assert "critical_alerting_report" not in report["missing_generators"]


def test_market_health_refresh_uses_public_data_only(tmp_path: Path) -> None:
    report = run_refresh(tmp_path)

    assert report["market_health_public_data_only"] is True
    assert report["private_endpoints_used"] is False
    assert report["exchange_private_access"] is False


def test_refresh_keeps_no_trade_policy_and_readiness_blocked(tmp_path: Path) -> None:
    report = run_refresh(tmp_path, blocked_readiness=True)

    assert report["status"] == "ok"
    assert report["system_health_status"] == "blocked"
    assert report["no_trade_policy_active"] is True
    assert report["readiness_blocked"] is True
    assert report["live_release_allowed"] is False
    assert report["readiness_approved"] is False
