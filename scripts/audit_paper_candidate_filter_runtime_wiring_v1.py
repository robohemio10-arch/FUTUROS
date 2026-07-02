#!/usr/bin/env python3
"""Audit paper-candidate filter runtime wiring without executing orders."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.execution.paper_candidate_filter_runtime_wiring import (  # noqa: E402
    SCHEMA_VERSION,
    apply_paper_candidate_filter_to_signals,
    summarize_runtime_wiring,
)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def sample_candidate_signals() -> list[dict[str, Any]]:
    return [
        {"symbol": "ETHUSDT", "pair": "ETH/USDT:USDT", "side": "long", "risk_approved": True},
        {"symbol": "ETHUSDT", "pair": "ETH/USDT:USDT", "side": "short", "risk_approved": True},
        {"symbol": "BTCUSDT", "pair": "BTC/USDT:USDT", "side": "long", "risk_approved": True},
        {"symbol": "BTCUSDT", "pair": "BTC/USDT:USDT", "side": "short", "risk_approved": True},
    ]


def build_audit_report(*, project_root: str | Path, mode: str = "paper_candidate") -> dict[str, Any]:
    root = Path(project_root).resolve()
    wiring = apply_paper_candidate_filter_to_signals(sample_candidate_signals(), runtime_mode=mode)
    summary = summarize_runtime_wiring(wiring)
    report: dict[str, Any] = {
        "status": "ok" if summary["runtime_wiring_status"] == "enabled" else "blocked",
        "reason": "paper_candidate_filter_runtime_wiring_available"
        if summary["runtime_wiring_status"] == "enabled"
        else "paper_candidate_filter_runtime_wiring_disabled",
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": _utc_now_iso(),
        "project_root": str(root),
        "discovered_candidate_entrypoints": [
            {
                "path": "smartcrypto/execution/signal_producer.py",
                "function": "build_active_signals",
                "integration": "paper_candidate_filter_applied_before_signal_payload_write",
            },
            {
                "path": "smartcrypto/execution/paper_candidate_filter_runtime_wiring.py",
                "function": "apply_paper_candidate_filter_to_signals",
                "integration": "isolated_paper_candidate_wrapper",
            },
        ],
        **summary,
        "validation_errors": [],
    }
    report["validation_errors"] = validate_runtime_wiring_report(report)
    if report["validation_errors"]:
        report["status"] = "blocked"
        report["reason"] = "paper_candidate_filter_runtime_wiring_validation_failed"
    return report


def validate_runtime_wiring_report(report: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version_mismatch")
    if report.get("runtime_wiring_status") != "enabled":
        errors.append("runtime_wiring_status_not_enabled")
    if report.get("paper_candidate_filter_called") is not True:
        errors.append("paper_candidate_filter_not_called")
    if report.get("paper_candidate_filter_enabled") is not True:
        errors.append("paper_candidate_filter_not_enabled")
    if report.get("adapter_integration_status") != "paper_adapter_available":
        errors.append("paper_adapter_not_available")
    if report.get("integration_status") != "runtime_wiring_available":
        errors.append("runtime_wiring_not_available")
    for key in (
        "ethusdt_long_blocked_before_execution",
        "ethusdt_short_blocked_before_execution",
        "btcusdt_long_allowed_to_paper_candidate",
        "btcusdt_short_allowed_to_paper_candidate",
    ):
        if report.get(key) is not True:
            errors.append(f"{key}_must_be_true")
    expected_false = (
        "live_behavior_changed",
        "canary_behavior_changed",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_risk",
        "updates_freqtrade",
        "updates_risk_manager",
        "updates_qlib_runtime",
        "updates_ai_shadow_runtime",
        "changes_model",
        "writes_runtime",
        "writes_sqlite",
        "writes_parquet",
    )
    for key in expected_false:
        if report.get(key) is not False:
            errors.append(f"{key}_must_be_false")
    if int(report.get("decision_event_count") or 0) < 4:
        errors.append("decision_event_count_below_4")
    if int(report.get("blocked_before_execution_count") or 0) != 2:
        errors.append("blocked_before_execution_count_mismatch")
    if int(report.get("allowed_to_candidate_count") or 0) != 2:
        errors.append("allowed_to_candidate_count_mismatch")
    if int(report.get("execution_submission_count") or 0) != 2:
        errors.append("execution_submission_count_mismatch")
    if int(report.get("blocked_submission_count") or 0) != 2:
        errors.append("blocked_submission_count_mismatch")
    return sorted(set(errors))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--mode", default="paper_candidate", help="Mode to audit. Defaults to paper_candidate.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_audit_report(project_root=args.project_root, mode=args.mode)
    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False))
    else:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
