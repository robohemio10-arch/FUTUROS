"""Contracts and immutable safety invariants for B06."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "paper_ab_testnet_chaos_readiness_v2"
EVIDENCE_SCHEMA_VERSION = "paper_ab_testnet_chaos_evidence_v2"
CONFIG_SCHEMA_VERSION = "paper_ab_testnet_chaos_readiness_config_v2"
DECISION_READY = "READY_FOR_30_DAY_SOAK"
DECISION_BLOCKED = "BLOCKED_BEFORE_SOAK"

DEFAULT_CONFIG_PATH = Path(
    "config/paper_ab_testnet_chaos_readiness_v2.json"
)
DEFAULT_REPORT_JSON = Path(
    "data/reports/paper_ab_testnet_chaos_readiness_v2.json"
)
DEFAULT_REPORT_MARKDOWN = Path(
    "data/reports/paper_ab_testnet_chaos_readiness_v2.md"
)
ALLOWED_REPORT_ROOT = Path("data/reports")

REQUIRED_TESTNET_STAGES = (
    "signal_created",
    "risk_approved",
    "order_submitted_testnet",
    "partial_fill_observed",
    "cancel_observed",
    "reconciliation_complete",
    "restart_recovery_complete",
)
REQUIRED_CHAOS_SCENARIOS = (
    "open_trade_restart",
    "qlib_unavailable",
    "signal_missing",
    "sqlite_locked",
    "disk_full",
    "clock_skew",
    "public_api_unavailable",
    "corrupted_report",
    "restart_loop",
    "reconciliation_recovery",
)

SAFETY_FLAGS: dict[str, bool] = {
    "research_only": True,
    "paper_only": True,
    "shadow_only": True,
    "testnet_evidence_only": True,
    "operational_authority": False,
    "live_trading_enabled": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "testnet_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
    "writes_runtime": False,
    "restarts_containers": False,
    "runs_training": False,
    "promotes_model": False,
    "automatic_promotion": False,
    "model_promotion_performed": False,
    "active_model_changed": False,
    "writes_active_registry": False,
    "writes_active_signals": False,
    "updates_freqtrade": False,
    "updates_risk_manager": False,
    "updates_qlib_runtime": False,
    "updates_ai_shadow_runtime": False,
    "starts_soak": False,
}


def mapping(value: Any) -> dict[str, Any]:
    """Return a mutable mapping or an empty fail-closed mapping."""

    return dict(value) if isinstance(value, Mapping) else {}


def mapping_list(value: Any) -> list[dict[str, Any]]:
    """Return only mapping items from a sequence-like evidence field."""

    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return []
    return [
        dict(item)
        for item in value
        if isinstance(item, Mapping)
    ]


def gate(
    passed: bool,
    reason: str,
    blockers: Sequence[str],
    **details: Any,
) -> dict[str, Any]:
    """Build a deterministic gate result."""

    return {
        "status": "pass" if passed else "blocked",
        "passed": passed,
        "reason": reason,
        "blockers": sorted(set(map(str, blockers))),
        "warnings": [],
        **details,
    }
