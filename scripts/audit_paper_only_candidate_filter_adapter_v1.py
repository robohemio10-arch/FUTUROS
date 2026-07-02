#!/usr/bin/env python3
"""Audit the paper-only candidate filter adapter without side effects."""

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

from smartcrypto.execution.paper_candidate_filter_adapter import (  # noqa: E402
    PAPER_CANDIDATE_MODE,
    SAFETY_FLAGS,
    SCHEMA_VERSION,
    PaperOnlyCandidateFilterAdapter,
)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def sample_proposals() -> list[dict[str, str]]:
    return [
        {"symbol": "ETHUSDT", "side": "long", "mode": PAPER_CANDIDATE_MODE},
        {"symbol": "ETHUSDT", "side": "short", "mode": PAPER_CANDIDATE_MODE},
        {"symbol": "BTCUSDT", "side": "long", "mode": PAPER_CANDIDATE_MODE},
        {"symbol": "BTCUSDT", "side": "short", "mode": PAPER_CANDIDATE_MODE},
    ]


def build_audit_report(*, project_root: str | Path, mode: str = PAPER_CANDIDATE_MODE) -> dict[str, Any]:
    root = Path(project_root).resolve()
    adapter = PaperOnlyCandidateFilterAdapter()
    decisions = [adapter.evaluate({**proposal, "mode": mode}, mode=mode).to_event() for proposal in sample_proposals()]
    blocked = [item for item in decisions if item.get("decision") == "BLOCK"]
    allowed = [item for item in decisions if item.get("decision") == "ALLOW"]
    filter_applied = any(item.get("filter_applied") is True for item in decisions)
    adapter_enabled = any(item.get("adapter_status") == "enabled" for item in decisions)
    report: dict[str, Any] = {
        "status": "ok" if adapter_enabled and filter_applied else "blocked",
        "reason": "paper_candidate_filter_adapter_available" if adapter_enabled else "paper_candidate_filter_adapter_disabled",
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": _utc_now_iso(),
        "project_root": str(root),
        "adapter_status": "enabled" if adapter_enabled else "disabled",
        "integration_status": "paper_adapter_available",
        "paper_candidate_filter_enabled": adapter_enabled,
        "filter_applied": filter_applied,
        "sample_decisions": decisions,
        "blocked_count": len(blocked),
        "allowed_count": len(allowed),
        "blocked_eth_long_count": _count_blocked(decisions, symbol="ETHUSDT", side="long"),
        "blocked_eth_short_count": _count_blocked(decisions, symbol="ETHUSDT", side="short"),
        "safety_flags": dict(SAFETY_FLAGS),
        "write_performed": False,
        **SAFETY_FLAGS,
    }
    report["validation_errors"] = validate_audit_report(report)
    if report["validation_errors"]:
        report["status"] = "blocked"
        report["reason"] = "paper_candidate_filter_adapter_validation_failed"
    return report


def _count_blocked(decisions: list[Mapping[str, Any]], *, symbol: str, side: str) -> int:
    return sum(
        1
        for item in decisions
        if item.get("decision") == "BLOCK"
        and item.get("symbol_norm") == symbol
        and item.get("side_norm") == side
    )


def validate_audit_report(report: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version_mismatch")
    if report.get("integration_status") != "paper_adapter_available":
        errors.append("integration_status_not_available")
    if report.get("live_behavior_changed") is not False:
        errors.append("live_behavior_changed_must_be_false")
    if report.get("canary_behavior_changed") is not False:
        errors.append("canary_behavior_changed_must_be_false")
    if report.get("sends_orders") is not False:
        errors.append("sends_orders_must_be_false")
    if report.get("exchange_private_access") is not False:
        errors.append("exchange_private_access_must_be_false")
    if report.get("changes_risk") is not False:
        errors.append("changes_risk_must_be_false")
    if report.get("writes_runtime") is not False:
        errors.append("writes_runtime_must_be_false")
    for key, expected in SAFETY_FLAGS.items():
        if report.get(key) is not expected:
            errors.append(f"{key}_must_be_{str(expected).lower()}")
        safety_flags = report.get("safety_flags")
        if not isinstance(safety_flags, Mapping) or safety_flags.get(key) is not expected:
            errors.append(f"safety_flags.{key}_must_be_{str(expected).lower()}")
    return sorted(set(errors))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--mode", default=PAPER_CANDIDATE_MODE, help="Audit mode. Defaults to paper_candidate.")
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
