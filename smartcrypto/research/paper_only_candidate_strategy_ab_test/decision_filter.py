"""Paper-only candidate decision filter for ETHUSDT survivor remediation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Literal

Decision = Literal["ALLOW", "BLOCK"]


BLOCKED_RULES: tuple[dict[str, str], ...] = (
    {
        "symbol_norm": "ETHUSDT",
        "side_norm": "long",
        "reason": "discarded_negative_survivor_ethusdt_long",
        "source_survivor_rule_id": "include__symbol_norm_ETHUSDT__side_norm_long",
    },
    {
        "symbol_norm": "ETHUSDT",
        "side_norm": "short",
        "reason": "discarded_negative_survivor_ethusdt_short",
        "source_survivor_rule_id": "include__symbol_norm_ETHUSDT__side_norm_short",
    },
)


@dataclass(frozen=True)
class CandidateDecision:
    decision: Decision
    symbol_norm: str
    side_norm: str
    reason: str
    candidate_filter_active: bool
    paper_only: bool = True
    candidate_only: bool = True
    live_behavior_changed: bool = False
    sends_orders: bool = False
    exchange_private_access: bool = False
    changes_risk: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "symbol_norm": self.symbol_norm,
            "side_norm": self.side_norm,
            "reason": self.reason,
            "candidate_filter_active": self.candidate_filter_active,
            "paper_only": self.paper_only,
            "candidate_only": self.candidate_only,
            "live_behavior_changed": self.live_behavior_changed,
            "sends_orders": self.sends_orders,
            "exchange_private_access": self.exchange_private_access,
            "changes_risk": self.changes_risk,
        }


def normalize_symbol(value: object) -> str:
    symbol = str(value or "").strip().upper()
    return symbol.replace("/", "").replace("-", "").replace("_", "")


def normalize_side(value: object) -> str:
    side = str(value or "").strip().lower()
    if side in {"buy", "long", "entry_long"}:
        return "long"
    if side in {"sell", "short", "entry_short"}:
        return "short"
    return side


class PaperOnlyCandidateDecisionFilter:
    """Deterministic paper-only candidate filter.

    The filter is intentionally narrow: it blocks the two negative ETHUSDT
    survivor cohorts in candidate/paper analysis only. It never submits orders,
    reads private exchange state or changes risk.
    """

    def __init__(self, *, active: bool = True) -> None:
        self.active = active

    def evaluate(self, proposal: Mapping[str, Any]) -> CandidateDecision:
        symbol_norm = normalize_symbol(proposal.get("symbol") or proposal.get("pair") or proposal.get("moeda"))
        side_norm = normalize_side(proposal.get("side") or proposal.get("fechar_side"))
        if not self.active:
            return CandidateDecision(
                decision="ALLOW",
                symbol_norm=symbol_norm,
                side_norm=side_norm,
                reason="candidate_filter_inactive",
                candidate_filter_active=False,
            )
        for rule in BLOCKED_RULES:
            if symbol_norm == rule["symbol_norm"] and side_norm == rule["side_norm"]:
                return CandidateDecision(
                    decision="BLOCK",
                    symbol_norm=symbol_norm,
                    side_norm=side_norm,
                    reason=rule["reason"],
                    candidate_filter_active=True,
                )
        return CandidateDecision(
            decision="ALLOW",
            symbol_norm=symbol_norm,
            side_norm=side_norm,
            reason="candidate_filter_allow",
            candidate_filter_active=True,
        )

    def definition(self) -> dict[str, Any]:
        return {
            "filter_name": "PaperOnlyCandidateDecisionFilter",
            "candidate_filter_active": self.active,
            "paper_only": True,
            "candidate_only": True,
            "live_behavior_changed": False,
            "blocked_rules": [dict(rule) for rule in BLOCKED_RULES],
        }
