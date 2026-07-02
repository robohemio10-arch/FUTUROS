"""Paper-only candidate adapter for the ETHUSDT candidate decision filter."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Mapping

from smartcrypto.research.paper_only_candidate_strategy_ab_test import (
    PaperOnlyCandidateDecisionFilter,
)

SCHEMA_VERSION = "paper_only_candidate_filter_adapter_v1"
PAPER_CANDIDATE_MODE = "paper_candidate"
BLOCKED_MODES = {"live", "canary", "production", "real"}

AdapterStatus = Literal["enabled", "disabled"]
AdapterDecision = Literal["ALLOW", "BLOCK"]

SAFETY_FLAGS: dict[str, bool] = {
    "live_behavior_changed": False,
    "canary_behavior_changed": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
    "updates_freqtrade": False,
    "updates_risk_manager": False,
    "updates_qlib_runtime": False,
    "updates_ai_shadow_runtime": False,
    "writes_runtime": False,
    "writes_sqlite": False,
    "writes_parquet": False,
}


@dataclass(frozen=True)
class PaperCandidateAdapterDecision:
    adapter_status: AdapterStatus
    integration_status: str
    mode: str
    paper_candidate_filter_enabled: bool
    filter_applied: bool
    decision: AdapterDecision
    reason: str
    symbol_norm: str
    side_norm: str
    event_type: str
    event_created_at_utc: str
    source: str
    live_behavior_changed: bool = False
    canary_behavior_changed: bool = False
    order_submission_enabled: bool = False
    real_order_submission_enabled: bool = False
    exchange_private_access: bool = False
    sends_orders: bool = False
    changes_risk: bool = False
    updates_freqtrade: bool = False
    updates_risk_manager: bool = False
    updates_qlib_runtime: bool = False
    updates_ai_shadow_runtime: bool = False
    writes_runtime: bool = False
    writes_sqlite: bool = False
    writes_parquet: bool = False

    def to_event(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = SCHEMA_VERSION
        payload["safety_flags"] = dict(SAFETY_FLAGS)
        return payload


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def normalize_mode(mode: object | None) -> str:
    return str(mode or "").strip().lower()


class PaperOnlyCandidateFilterAdapter:
    """Adapter that applies the candidate filter only in paper_candidate mode.

    The adapter has no order submission, exchange access, risk mutation or
    runtime writes. It only returns a structured decision event for the caller.
    """

    def __init__(self, *, filter_: PaperOnlyCandidateDecisionFilter | None = None) -> None:
        self.filter = filter_ or PaperOnlyCandidateDecisionFilter(active=True)

    def evaluate(self, proposed_trade: Mapping[str, Any], *, mode: str | None = None) -> PaperCandidateAdapterDecision:
        normalized_mode = normalize_mode(mode or proposed_trade.get("mode"))
        if normalized_mode != PAPER_CANDIDATE_MODE:
            reason = "adapter_disabled_non_paper_candidate_mode"
            if normalized_mode in BLOCKED_MODES:
                reason = f"adapter_rejects_{normalized_mode}_mode"
            inactive_filter = PaperOnlyCandidateDecisionFilter(active=False)
            decision = inactive_filter.evaluate(proposed_trade)
            return PaperCandidateAdapterDecision(
                adapter_status="disabled",
                integration_status="paper_adapter_available",
                mode=normalized_mode or "unspecified",
                paper_candidate_filter_enabled=False,
                filter_applied=False,
                decision="ALLOW",
                reason=reason,
                symbol_norm=decision.symbol_norm,
                side_norm=decision.side_norm,
                event_type="paper_candidate_filter_adapter_decision",
                event_created_at_utc=_utc_now_iso(),
                source="paper_only_candidate_filter_adapter",
            )

        decision = self.filter.evaluate(proposed_trade)
        return PaperCandidateAdapterDecision(
            adapter_status="enabled",
            integration_status="paper_adapter_available",
            mode=PAPER_CANDIDATE_MODE,
            paper_candidate_filter_enabled=True,
            filter_applied=True,
            decision=decision.decision,
            reason=decision.reason,
            symbol_norm=decision.symbol_norm,
            side_norm=decision.side_norm,
            event_type="paper_candidate_filter_adapter_decision",
            event_created_at_utc=_utc_now_iso(),
            source="paper_only_candidate_filter_adapter",
        )


def evaluate_paper_candidate_filter(
    proposed_trade: Mapping[str, Any],
    *,
    mode: str | None = None,
) -> dict[str, Any]:
    """Return a structured adapter decision without side effects."""

    return PaperOnlyCandidateFilterAdapter().evaluate(proposed_trade, mode=mode).to_event()
