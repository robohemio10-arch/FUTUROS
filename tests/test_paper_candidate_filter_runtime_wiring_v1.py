from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from scripts.audit_paper_candidate_filter_runtime_wiring_v1 import build_audit_report
from smartcrypto.execution.paper_candidate_filter_runtime_wiring import (
    apply_paper_candidate_filter_to_signals,
    summarize_runtime_wiring,
)
from smartcrypto.execution.signal_producer import build_active_signals


def _signals() -> list[dict[str, object]]:
    return [
        {"symbol": "ETHUSDT", "pair": "ETH/USDT:USDT", "side": "long", "risk_approved": True},
        {"symbol": "ETHUSDT", "pair": "ETH/USDT:USDT", "side": "short", "risk_approved": True},
        {"symbol": "BTCUSDT", "pair": "BTC/USDT:USDT", "side": "long", "risk_approved": True},
        {"symbol": "BTCUSDT", "pair": "BTC/USDT:USDT", "side": "short", "risk_approved": True},
    ]


def test_runtime_wiring_disabled_outside_paper_candidate() -> None:
    wiring = apply_paper_candidate_filter_to_signals(_signals(), runtime_mode="paper")

    assert wiring["runtime_wiring_status"] == "disabled"
    assert wiring["paper_candidate_filter_called"] is False
    assert wiring["filter_applied"] is False
    assert len(wiring["allowed_signals"]) == 4


def test_runtime_wiring_rejects_live_mode() -> None:
    wiring = apply_paper_candidate_filter_to_signals(_signals(), runtime_mode="live")

    assert wiring["runtime_wiring_status"] == "disabled"
    assert wiring["paper_candidate_filter_called"] is False
    assert wiring["live_behavior_changed"] is False
    assert wiring["execution_submission_count"] == 4


def test_runtime_wiring_rejects_canary_mode() -> None:
    wiring = apply_paper_candidate_filter_to_signals(_signals(), runtime_mode="canary")

    assert wiring["runtime_wiring_status"] == "disabled"
    assert wiring["paper_candidate_filter_called"] is False
    assert wiring["canary_behavior_changed"] is False
    assert wiring["execution_submission_count"] == 4


def test_runtime_wiring_calls_adapter_in_paper_candidate_mode() -> None:
    wiring = apply_paper_candidate_filter_to_signals(_signals(), runtime_mode="paper_candidate")

    assert wiring["runtime_wiring_status"] == "enabled"
    assert wiring["paper_candidate_filter_called"] is True
    assert wiring["paper_candidate_filter_enabled"] is True
    assert wiring["adapter_integration_status"] == "paper_adapter_available"


def test_ethusdt_long_blocked_before_execution() -> None:
    summary = summarize_runtime_wiring(apply_paper_candidate_filter_to_signals(_signals(), runtime_mode="paper_candidate"))

    assert summary["ethusdt_long_blocked_before_execution"] is True
    assert any(
        item["decision"] == "BLOCK" and item["symbol_norm"] == "ETHUSDT" and item["side_norm"] == "long"
        for item in summary["decision_log_sample"]
    )


def test_ethusdt_short_blocked_before_execution() -> None:
    summary = summarize_runtime_wiring(apply_paper_candidate_filter_to_signals(_signals(), runtime_mode="paper_candidate"))

    assert summary["ethusdt_short_blocked_before_execution"] is True
    assert any(
        item["decision"] == "BLOCK" and item["symbol_norm"] == "ETHUSDT" and item["side_norm"] == "short"
        for item in summary["decision_log_sample"]
    )


def test_btcusdt_long_allowed_to_candidate_execution() -> None:
    summary = summarize_runtime_wiring(apply_paper_candidate_filter_to_signals(_signals(), runtime_mode="paper_candidate"))

    assert summary["btcusdt_long_allowed_to_paper_candidate"] is True


def test_btcusdt_short_allowed_to_candidate_execution() -> None:
    summary = summarize_runtime_wiring(apply_paper_candidate_filter_to_signals(_signals(), runtime_mode="paper_candidate"))

    assert summary["btcusdt_short_allowed_to_paper_candidate"] is True


def test_blocked_decisions_do_not_submit_to_candidate_executor() -> None:
    wiring = apply_paper_candidate_filter_to_signals(_signals(), runtime_mode="paper_candidate")

    blocked = [item for item in wiring["decision_events"] if item["decision"] == "BLOCK"]
    assert blocked
    assert all(item["blocked_before_execution"] is True for item in blocked)
    assert all(item["submitted_to_candidate_executor"] is False for item in blocked)
    assert wiring["blocked_submission_count"] == 2


def test_allowed_decisions_submit_to_candidate_executor() -> None:
    wiring = apply_paper_candidate_filter_to_signals(_signals(), runtime_mode="paper_candidate")

    allowed = [item for item in wiring["decision_events"] if item["decision"] == "ALLOW"]
    assert allowed
    assert all(item["submitted_to_candidate_executor"] is True for item in allowed)
    assert wiring["execution_submission_count"] == 2


def test_decision_events_are_structured() -> None:
    wiring = apply_paper_candidate_filter_to_signals(_signals(), runtime_mode="paper_candidate")
    event = wiring["decision_events"][0]

    assert event["schema_version"] == "paper_only_candidate_filter_adapter_v1"
    assert event["runtime_wiring_schema_version"] == "paper_candidate_filter_runtime_wiring_v1"
    assert event["event_type"] == "paper_candidate_filter_adapter_decision"
    assert event["adapter_integration_status"] == "paper_adapter_available"
    assert event["safety_flags"]["sends_orders"] is False


def test_runtime_wiring_never_sends_real_orders() -> None:
    summary = summarize_runtime_wiring(apply_paper_candidate_filter_to_signals(_signals(), runtime_mode="paper_candidate"))

    assert summary["sends_orders"] is False
    assert summary["order_submission_enabled"] is False
    assert summary["real_order_submission_enabled"] is False


def test_runtime_wiring_never_accesses_exchange_private() -> None:
    summary = summarize_runtime_wiring(apply_paper_candidate_filter_to_signals(_signals(), runtime_mode="paper_candidate"))

    assert summary["exchange_private_access"] is False


def test_runtime_wiring_does_not_change_risk() -> None:
    summary = summarize_runtime_wiring(apply_paper_candidate_filter_to_signals(_signals(), runtime_mode="paper_candidate"))

    assert summary["changes_risk"] is False
    assert summary["updates_risk_manager"] is False


def test_signal_producer_calls_wiring_before_payload_write(monkeypatch, tmp_path: Path) -> None:
    predictions_path = tmp_path / "predictions.parquet"
    primary_path = tmp_path / "freqtrade_signals.json"
    pinned_path = tmp_path / "active_freqtrade_signals.json"
    report_path = tmp_path / "phase13_signal_producer_report.json"
    pd.DataFrame(
        [
            {"symbol": "ETHUSDT", "prob_up": 0.95},
            {"symbol": "ETHUSDT", "prob_up": 0.05},
            {"symbol": "BTCUSDT", "prob_up": 0.85},
            {"symbol": "BTCUSDT", "prob_up": 0.15},
        ]
    ).to_parquet(predictions_path)

    monkeypatch.setattr(
        "smartcrypto.execution.signal_producer.inspect_qlib_prediction_freshness",
        lambda *args, **kwargs: {"freshness_status": "fresh", "input_data_status": "input_data_fresh", "rows": 4},
    )
    report = build_active_signals(
        {
            "runtime_mode": "paper_candidate",
            "paths": {
                "predictions": str(predictions_path),
                "primary_signals": str(primary_path),
                "pinned_signals": str(pinned_path),
                "report": str(report_path),
            },
            "policy": {
                "long_probability": 0.55,
                "short_probability": 0.45,
                "min_confidence": 0.0,
                "max_signals": 4,
                "never_overwrite_with_empty": False,
            },
            "risk": {"max_position_usdt": 50.0, "leverage": 2.0},
        },
        force_from_predictions=True,
    )

    wiring = report["paper_candidate_filter_runtime_wiring"]
    payload = json.loads(primary_path.read_text(encoding="utf-8"))
    assert wiring["runtime_wiring_status"] == "enabled"
    assert wiring["paper_candidate_filter_called"] is True
    assert wiring["blocked_before_execution_count"] == 2
    assert wiring["execution_submission_count"] == 2
    assert {item["symbol"] for item in payload["signals"]} == {"BTCUSDT"}


def test_auditor_reports_runtime_wiring_available(tmp_path: Path) -> None:
    report = build_audit_report(project_root=tmp_path)

    assert report["status"] == "ok"
    assert report["runtime_wiring_status"] == "enabled"
    assert report["paper_candidate_filter_called"] is True
    assert report["integration_status"] == "runtime_wiring_available"
    assert report["blocked_before_execution_count"] == 2
    assert report["allowed_to_candidate_count"] == 2


def test_cli_json_executes() -> None:
    script = Path("scripts/audit_paper_candidate_filter_runtime_wiring_v1.py")
    completed = subprocess.run(
        [sys.executable, str(script), "--project-root", ".", "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["status"] == "ok"
    assert payload["runtime_wiring_status"] == "enabled"
    assert payload["paper_candidate_filter_called"] is True
    assert payload["ethusdt_long_blocked_before_execution"] is True
    assert payload["ethusdt_short_blocked_before_execution"] is True
    assert payload["sends_orders"] is False
