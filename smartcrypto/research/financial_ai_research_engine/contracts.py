"""Immutable contracts for the research-only financial AI engine."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


SCHEMA_VERSION = "financial_ai_research_engine_v1"
FINANCIAL_EV_SEMANTICS = "EXPECTED_NET_PNL_USDT"
REMAINING_EV_SEMANTICS = "EXPECTED_NET_PNL_USDT_FROM_EVALUATION_TIME"
DECISION = "MANTER_EM_RESEARCH"
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")

MINIMUM_TRAIN_ROWS = 200
MINIMUM_OOS_ROWS = 100
MINIMUM_POSITIVE_ROWS = 40
MINIMUM_NEGATIVE_ROWS = 40
MINIMUM_PROFIT_FACTOR = 1.10
MINIMUM_AUC = 0.55
MAXIMUM_ECE = 0.05
MAXIMUM_BRIER = 0.24
DEFAULT_EMBARGO_SECONDS = 86_400

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "operational_authority": False,
    "writes_sqlite": False,
    "writes_runtime": False,
    "writes_active_signals": False,
    "writes_active_model": False,
    "writes_active_registry": False,
    "trains_active_model": False,
    "promotes_model": False,
    "updates_qlib_runtime": False,
    "updates_ai_shadow_runtime": False,
    "changes_strategy": False,
    "changes_risk": False,
    "changes_stake": False,
    "changes_leverage": False,
    "changes_max_open_trades": False,
    "sends_orders": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
}

EXTENDED_POST_TRADE_OUTCOMES = frozenset(
    {
        "close_profit",
        "close_profit_abs",
        "realized_profit",
        "profit_abs",
        "profit_ratio",
        "normalized_net_return",
        "return_on_stake",
        "holding_minutes",
        "duration_seconds",
        "mfe",
        "mae",
        "reported_realized_pnl_usdt",
        "realized_net_pnl_usdt",
        "positive_net_outcome",
        "fee_open",
        "fee_close",
        "fee_open_cost",
        "fee_close_cost",
        "fee_total_cost",
        "fees",
        "trading_fee",
        "funding_fees",
        "funding_fee",
        "funding_revenue",
        "funding_cost",
        "funding_net",
        "estimated_slippage_cost",
        "estimated_spread_cost",
        "total_observed_cost",
        "total_estimated_cost",
        "exit_reason",
        "exit_price",
        "close_rate",
        "close_date",
        "close_time",
        "close_time_utc",
        "max_rate",
        "min_rate",
    }
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def utc_iso(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def finite_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def valid_sha256(value: Any) -> bool:
    return bool(SHA256_PATTERN.fullmatch(str(value or "").strip()))


def stable_hash(payload: Mapping[str, Any]) -> str:
    rendered = json.dumps(
        dict(payload),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def normalize_symbol(value: Any) -> str:
    return (
        str(value or "")
        .upper()
        .strip()
        .replace(":USDT", "")
        .replace("/", "")
        .replace("-", "")
    )


def normalize_side(value: Any) -> str | None:
    normalized = str(value or "").upper().strip()
    normalized = {"BUY": "LONG", "SELL": "SHORT"}.get(normalized, normalized)
    return normalized if normalized in {"LONG", "SHORT"} else None


@dataclass(frozen=True)
class EngineConfig:
    minimum_train_rows: int = MINIMUM_TRAIN_ROWS
    minimum_oos_rows: int = MINIMUM_OOS_ROWS
    minimum_positive_rows: int = MINIMUM_POSITIVE_ROWS
    minimum_negative_rows: int = MINIMUM_NEGATIVE_ROWS
    embargo_seconds: int = DEFAULT_EMBARGO_SECONDS
    minimum_profit_factor: float = MINIMUM_PROFIT_FACTOR
    minimum_auc: float = MINIMUM_AUC
    maximum_ece: float = MAXIMUM_ECE
    maximum_brier: float = MAXIMUM_BRIER


@dataclass(frozen=True)
class FinancialCandidateEstimate:
    estimate_id: str
    estimate_subject_id: str
    candidate_id: str | None
    candidate_linkage_status: str
    observed_at_utc: str
    estimate_scope: str
    point_in_time_consumable: bool
    branch2_compatible: bool
    candidate_ev: float | None
    financial_ev_semantics: str
    financial_ev_generated_at_utc: str
    financial_ev_available_at_utc: str
    financial_ev_source_hash: str
    financial_model_version: str
    financial_win_probability: float | None
    candidate_ev_lower: float | None
    candidate_ev_upper: float | None
    uncertainty_status: str
    position_remaining_ev: float | None
    remaining_position_ev_semantics: str
    remaining_position_ev_generated_at_utc: str | None
    remaining_position_ev_available_at_utc: str | None
    remaining_position_ev_source_hash: str | None
    switching_cost_estimate: float | None
    switching_cost_status: str
    financial_estimate_trusted: bool
    candidate_ev_status: str
    candidate_ev_blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["candidate_ev_blockers"] = list(self.candidate_ev_blockers)
        return payload


@dataclass(frozen=True)
class RemainingPositionEstimate:
    position_trade_id: str
    evaluated_at_utc: str
    remaining_position_ev: None = None
    remaining_position_ev_semantics: str = REMAINING_EV_SEMANTICS
    remaining_position_model_version: None = None
    remaining_position_ev_status: str = "INSUFFICIENT_TRAINING_EVIDENCE"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
