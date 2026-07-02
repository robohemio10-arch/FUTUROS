from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.audit_paper_only_candidate_filter_adapter_v1 import build_audit_report
from smartcrypto.execution.paper_candidate_filter_adapter import (
    PaperOnlyCandidateFilterAdapter,
    evaluate_paper_candidate_filter,
)


def test_adapter_disabled_by_default() -> None:
    event = evaluate_paper_candidate_filter({"symbol": "ETHUSDT", "side": "long"})

    assert event["adapter_status"] == "disabled"
    assert event["filter_applied"] is False
    assert event["decision"] == "ALLOW"
    assert event["live_behavior_changed"] is False


def test_adapter_rejects_live_mode() -> None:
    event = evaluate_paper_candidate_filter({"symbol": "ETHUSDT", "side": "long"}, mode="live")

    assert event["adapter_status"] == "disabled"
    assert event["filter_applied"] is False
    assert event["reason"] == "adapter_rejects_live_mode"
    assert event["live_behavior_changed"] is False


def test_adapter_rejects_canary_mode() -> None:
    event = evaluate_paper_candidate_filter({"symbol": "ETHUSDT", "side": "short"}, mode="canary")

    assert event["adapter_status"] == "disabled"
    assert event["filter_applied"] is False
    assert event["reason"] == "adapter_rejects_canary_mode"
    assert event["canary_behavior_changed"] is False


def test_adapter_enabled_only_for_paper_candidate() -> None:
    adapter = PaperOnlyCandidateFilterAdapter()
    disabled = adapter.evaluate({"symbol": "BTCUSDT", "side": "long"}, mode="paper")
    enabled = adapter.evaluate({"symbol": "BTCUSDT", "side": "long"}, mode="paper_candidate")

    assert disabled.adapter_status == "disabled"
    assert disabled.filter_applied is False
    assert enabled.adapter_status == "enabled"
    assert enabled.filter_applied is True


def test_adapter_blocks_ethusdt_long() -> None:
    event = evaluate_paper_candidate_filter({"symbol": "ETHUSDT", "side": "long"}, mode="paper_candidate")

    assert event["decision"] == "BLOCK"
    assert event["reason"] == "discarded_negative_survivor_ethusdt_long"


def test_adapter_blocks_ethusdt_short() -> None:
    event = evaluate_paper_candidate_filter({"symbol": "ETHUSDT", "side": "short"}, mode="paper_candidate")

    assert event["decision"] == "BLOCK"
    assert event["reason"] == "discarded_negative_survivor_ethusdt_short"


def test_adapter_allows_btcusdt_long() -> None:
    event = evaluate_paper_candidate_filter({"symbol": "BTCUSDT", "side": "long"}, mode="paper_candidate")

    assert event["decision"] == "ALLOW"
    assert event["reason"] == "candidate_filter_allow"


def test_adapter_allows_btcusdt_short() -> None:
    event = evaluate_paper_candidate_filter({"symbol": "BTCUSDT", "side": "short"}, mode="paper_candidate")

    assert event["decision"] == "ALLOW"
    assert event["reason"] == "candidate_filter_allow"


def test_adapter_decision_log_is_structured() -> None:
    event = evaluate_paper_candidate_filter({"symbol": "ETH/USDT", "side": "SHORT"}, mode="paper_candidate")

    assert event["schema_version"] == "paper_only_candidate_filter_adapter_v1"
    assert event["event_type"] == "paper_candidate_filter_adapter_decision"
    assert event["symbol_norm"] == "ETHUSDT"
    assert event["side_norm"] == "short"
    assert isinstance(event["event_created_at_utc"], str)
    assert event["safety_flags"]["sends_orders"] is False


def test_adapter_never_sends_orders() -> None:
    event = evaluate_paper_candidate_filter({"symbol": "ETHUSDT", "side": "long"}, mode="paper_candidate")

    assert event["sends_orders"] is False
    assert event["order_submission_enabled"] is False
    assert event["real_order_submission_enabled"] is False


def test_adapter_never_accesses_exchange_private() -> None:
    event = evaluate_paper_candidate_filter({"symbol": "ETHUSDT", "side": "long"}, mode="paper_candidate")

    assert event["exchange_private_access"] is False


def test_adapter_does_not_change_risk() -> None:
    event = evaluate_paper_candidate_filter({"symbol": "ETHUSDT", "side": "long"}, mode="paper_candidate")

    assert event["changes_risk"] is False
    assert event["updates_risk_manager"] is False


def test_auditor_reports_paper_adapter_available(tmp_path: Path) -> None:
    report = build_audit_report(project_root=tmp_path)

    assert report["status"] == "ok"
    assert report["integration_status"] == "paper_adapter_available"
    assert report["paper_candidate_filter_enabled"] is True
    assert report["filter_applied"] is True
    assert report["blocked_eth_long_count"] == 1
    assert report["blocked_eth_short_count"] == 1
    assert report["live_behavior_changed"] is False
    assert report["canary_behavior_changed"] is False
    assert report["writes_runtime"] is False


def test_cli_json_executes() -> None:
    script = Path("scripts/audit_paper_only_candidate_filter_adapter_v1.py")
    completed = subprocess.run(
        [sys.executable, str(script), "--project-root", ".", "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["status"] == "ok"
    assert payload["integration_status"] == "paper_adapter_available"
    assert payload["paper_candidate_filter_enabled"] is True
    assert payload["filter_applied"] is True
    assert payload["blocked_eth_long_count"] >= 1
    assert payload["blocked_eth_short_count"] >= 1
    assert payload["sends_orders"] is False
    assert payload["exchange_private_access"] is False
