from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from smartcrypto.ops.critical_alerting import build_critical_alerting_report
from smartcrypto.ops.financial_event_log import (
    EVENT_TYPES,
    FinancialEventLog,
    FinancialEventLogError,
)


ROOT = Path(__file__).resolve().parents[1]


def append_event(log_path: Path, event_type: str, **overrides):
    logger = FinancialEventLog(log_path)
    payload = {
        "event_type": event_type,
        "correlation_id": overrides.pop("correlation_id", "corr-1"),
        "event_severity": overrides.pop("event_severity", "info"),
        "event_status": overrides.pop("event_status", "ok"),
        "source": overrides.pop("source", "unit_test"),
    }
    payload.update(overrides)
    return logger.append(**payload)


def test_financial_event_log_writes_jsonl_append_only(tmp_path):
    log_path = tmp_path / "financial_event_log.jsonl"
    append_event(log_path, "signal_generated", correlation_id="corr-a")
    first_size = log_path.stat().st_size
    append_event(log_path, "risk_approved", correlation_id="corr-a")

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert log_path.stat().st_size > first_size
    assert json.loads(lines[0])["event_type"] == "signal_generated"
    assert json.loads(lines[1])["event_type"] == "risk_approved"


def test_financial_event_log_requires_correlation_id(tmp_path):
    with pytest.raises(FinancialEventLogError, match="missing_correlation_id"):
        append_event(tmp_path / "events.jsonl", "signal_generated", correlation_id="")


def test_financial_event_log_blocks_invalid_event_type(tmp_path):
    with pytest.raises(FinancialEventLogError, match="invalid_event_type"):
        append_event(tmp_path / "events.jsonl", "real_order_sent")


def test_financial_event_log_blocks_invalid_severity(tmp_path):
    with pytest.raises(FinancialEventLogError, match="invalid_event_severity"):
        append_event(tmp_path / "events.jsonl", "signal_generated", event_severity="fatal")


def test_financial_event_log_blocks_unsafe_safety_flags(tmp_path):
    with pytest.raises(FinancialEventLogError, match="unsafe_safety_flags"):
        append_event(
            tmp_path / "events.jsonl",
            "risk_approved",
            safety_overrides={"live_trading_enabled": True, "order_submission_enabled": True},
        )


def test_financial_event_log_aggregates_by_type_and_severity(tmp_path):
    log_path = tmp_path / "events.jsonl"
    append_event(log_path, "signal_generated", event_severity="info")
    append_event(log_path, "risk_rejected", event_severity="warning", event_status="blocked")
    append_event(log_path, "backup_failed", event_severity="critical", event_status="open")

    summary = FinancialEventLog(log_path).summary()

    assert summary["total_events"] == 3
    assert summary["events_by_type"]["risk_rejected"] == 1
    assert summary["events_by_severity"]["critical"] == 1
    assert summary["open_incidents"] == 1


def test_financial_event_log_filters_by_correlation_id(tmp_path):
    log_path = tmp_path / "events.jsonl"
    append_event(log_path, "signal_generated", correlation_id="corr-a")
    append_event(log_path, "risk_rejected", correlation_id="corr-b")

    rows = FinancialEventLog(log_path).read_events(correlation_id="corr-b")

    assert len(rows) == 1
    assert rows[0]["event_type"] == "risk_rejected"


def test_critical_alerting_reports_missing_log(tmp_path):
    report = build_critical_alerting_report(
        event_log_path=tmp_path / "missing.jsonl",
        report_path=None,
        strict=False,
    )

    assert report["status"] == "missing_data"
    assert report["reason"] == "missing_event_log"


def test_critical_alerting_detects_kill_switch(tmp_path):
    log_path = tmp_path / "events.jsonl"
    append_event(log_path, "kill_switch_triggered", event_severity="critical", event_status="open")

    report = build_critical_alerting_report(event_log_path=log_path, report_path=None)

    assert report["status"] == "blocked"
    assert "kill_switch_triggered_critical" in report["blocked_findings"]


def test_critical_alerting_detects_state_divergence(tmp_path):
    log_path = tmp_path / "events.jsonl"
    append_event(log_path, "state_divergence_detected", event_severity="critical", event_status="open")

    report = build_critical_alerting_report(event_log_path=log_path, report_path=None)

    assert report["status"] == "blocked"
    assert "state_divergence_detected_critical" in report["blocked_findings"]


def test_critical_alerting_detects_backup_restore_failures(tmp_path):
    log_path = tmp_path / "events.jsonl"
    append_event(log_path, "backup_failed", event_severity="critical", event_status="open")
    append_event(log_path, "restore_failed", event_severity="critical", event_status="open")

    report = build_critical_alerting_report(event_log_path=log_path, report_path=None)

    assert report["status"] == "blocked"
    assert "backup_failed_critical" in report["blocked_findings"]
    assert "restore_failed_critical" in report["blocked_findings"]


def test_critical_alerting_detects_market_data_blocks(tmp_path):
    log_path = tmp_path / "events.jsonl"
    for event_type in ("market_data_stale", "spread_blocked", "liquidity_blocked", "latency_blocked"):
        append_event(log_path, event_type, event_severity="critical", event_status="blocked")

    report = build_critical_alerting_report(event_log_path=log_path, report_path=None)

    assert report["status"] == "blocked"
    assert "market_data_stale_critical" in report["blocked_findings"]
    assert "spread_blocked_critical" in report["blocked_findings"]
    assert "liquidity_blocked_critical" in report["blocked_findings"]
    assert "latency_blocked_critical" in report["blocked_findings"]


def test_critical_alerting_detects_repeated_risk_rejections(tmp_path):
    log_path = tmp_path / "events.jsonl"
    for index in range(4):
        append_event(log_path, "risk_rejected", correlation_id=f"corr-{index}", event_severity="warning", event_status="blocked")

    report = build_critical_alerting_report(
        event_log_path=log_path,
        report_path=None,
        max_risk_rejections=3,
    )

    assert report["status"] == "blocked"
    assert "repeated_risk_rejected" in report["blocked_findings"]


def test_critical_alerting_is_read_only(tmp_path):
    log_path = tmp_path / "events.jsonl"
    append_event(log_path, "signal_generated")
    before = log_path.read_bytes()

    build_critical_alerting_report(event_log_path=log_path, report_path=None)

    assert log_path.read_bytes() == before


def test_cli_run_financial_event_log_audit_runs_successfully(tmp_path):
    log_path = tmp_path / "events.jsonl"
    report_path = tmp_path / "critical_alerting_report.json"
    append_event(log_path, "signal_generated")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_financial_event_log_audit.py"),
            "--event-log",
            str(log_path),
            "--alert-report",
            str(report_path),
            "--max-risk-rejections",
            "5",
            "--max-prediction-stale",
            "3",
            "--strict",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["summary"]["total_events"] == 1
    assert report_path.exists()


def test_does_not_touch_training_dataset_or_trades_master(tmp_path):
    trades_master = tmp_path / "trades_master.parquet"
    training_dataset = tmp_path / "training_dataset.parquet"
    trades_master.write_text("master", encoding="utf-8")
    training_dataset.write_text("training", encoding="utf-8")
    before = {trades_master: trades_master.read_text(encoding="utf-8"), training_dataset: training_dataset.read_text(encoding="utf-8")}

    log_path = tmp_path / "events.jsonl"
    append_event(log_path, "signal_generated")
    build_critical_alerting_report(event_log_path=log_path, report_path=None)

    assert {path: path.read_text(encoding="utf-8") for path in before} == before


def test_does_not_touch_registry_models_signal_producer_risk_manager_or_freqtrade(tmp_path):
    sentinels = [
        tmp_path / "model_registry.json",
        tmp_path / "shadow_model.pkl",
        tmp_path / "active_freqtrade_signals.json",
        tmp_path / "risk_manager.yml",
        tmp_path / "tradesv3.paper.sqlite",
    ]
    for sentinel in sentinels:
        sentinel.write_text(f"sentinel:{sentinel.name}", encoding="utf-8")
    before = {path: path.read_text(encoding="utf-8") for path in sentinels}

    log_path = tmp_path / "events.jsonl"
    append_event(log_path, "signal_generated")
    build_critical_alerting_report(event_log_path=log_path, report_path=None)

    assert {path: path.read_text(encoding="utf-8") for path in sentinels} == before


def test_all_required_event_types_are_supported():
    assert {
        "signal_generated",
        "risk_rejected",
        "reconciliation_required",
        "paper_session_blocked",
    }.issubset(EVENT_TYPES)
