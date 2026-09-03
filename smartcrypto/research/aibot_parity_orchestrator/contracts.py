"""Immutable contracts for the AIBOT-Parity W12/W13 read-only orchestration layer."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


PIPELINE_SCHEMA_VERSION = "aibot_parity_e2e_snapshot_v1"
REQUIRED_SOURCE_NAMES = (
    "research_council",
    "market_intelligence",
    "ensemble_abstention",
    "opportunity_book",
    "portfolio_allocator",
)
OPTIONAL_SOURCE_NAMES = (
    "trader_master_benchmark",
    "portfolio_alphas_fleet",
    "relative_value",
    "execution_intelligence",
    "risk_budget",
    "treasury",
    "qlib_security",
    "ai_shadow",
    "riskmanager_shadow",
)
ALLOWED_SOURCE_NAMES = frozenset((*REQUIRED_SOURCE_NAMES, *OPTIONAL_SOURCE_NAMES))

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "operational_authority": False,
    "writes_active_signals": False,
    "signal_published": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "changes_risk": False,
    "changes_model": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
}


class PipelineStatus(str, Enum):
    READY_SHADOW = "READY_SHADOW"
    ABSTAIN = "ABSTAIN"
    BLOCKED = "BLOCKED"


class PointInTimeStatus(str, Enum):
    VALID = "VALID"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


class FrozenContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
        allow_inf_nan=False,
    )


def require_utc(value: datetime) -> datetime:
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None:
        raise ValueError("timestamp_must_be_timezone_aware")
    if offset.total_seconds() != 0:
        raise ValueError("timestamp_must_use_utc_offset_zero")
    return value.astimezone(timezone.utc)


def canonical_sha256(payload: Any) -> str:
    rendered = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def stable_id(prefix: str, payload: Any) -> str:
    return f"{prefix}-{canonical_sha256(payload)}"


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    raise TypeError(f"unsupported_json_type:{type(value).__name__}")


class AibotParityPipelineRequest(FrozenContract):
    schema_version: Literal["aibot_parity_pipeline_request_v1"] = (
        "aibot_parity_pipeline_request_v1"
    )
    request_id: str = Field(min_length=1, max_length=180)
    decision_time_utc: datetime
    sources: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @field_validator("decision_time_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @field_validator("sources")
    @classmethod
    def _validate_sources(
        cls, value: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        unknown = sorted(set(value) - ALLOWED_SOURCE_NAMES)
        if unknown:
            raise ValueError("unknown_pipeline_sources:" + ",".join(unknown))
        return value


class PipelineSourceView(FrozenContract):
    source_name: str
    status: str
    point_in_time_status: PointInTimeStatus
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_time_utc: datetime | None = None
    reason: str | None = None

    @field_validator("evidence_time_utc")
    @classmethod
    def _validate_optional_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)


class AibotParityPipelineSnapshot(FrozenContract):
    schema_version: Literal["aibot_parity_e2e_snapshot_v1"] = (
        "aibot_parity_e2e_snapshot_v1"
    )
    cycle_id: str
    request_id: str
    decision_time_utc: datetime
    created_at_utc: datetime
    status: PipelineStatus
    reason: str
    final_action: Literal["ABSTAIN", "WOULD_SIGNAL"]
    would_signal: bool
    signal_published: Literal[False] = False
    writes_active_signals: Literal[False] = False
    operational_authority: Literal[False] = False
    riskmanager_final_authority: Literal[True] = True
    qlib_status: str
    qlib_blocked_external: bool
    ensemble_action: str
    riskmanager_shadow_decision: str
    selected_candidate_ids: tuple[str, ...] = ()
    required_sources_present: tuple[str, ...] = ()
    missing_required_sources: tuple[str, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    source_views: tuple[PipelineSourceView, ...] = ()
    dashboard: dict[str, dict[str, Any]] = Field(default_factory=dict)
    safety: dict[str, bool] = Field(default_factory=lambda: dict(SAFETY_FLAGS))

    @field_validator("decision_time_utc", "created_at_utc")
    @classmethod
    def _validate_snapshot_time(cls, value: datetime) -> datetime:
        return require_utc(value)
