"""Point-in-time contracts for research-only Market Intelligence."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

SCHEMA_VERSION: Literal["market_intelligence_v1"] = "market_intelligence_v1"
EVENT_SCHEMA_VERSION: Literal["market_intelligence_event_v1"] = "market_intelligence_event_v1"
SNAPSHOT_SCHEMA_VERSION: Literal["market_intelligence_snapshot_v1"] = "market_intelligence_snapshot_v1"
CONFIG_SCHEMA_VERSION: Literal["market_intelligence_config_v1"] = "market_intelligence_config_v1"

CORE_FEATURE_FAMILIES = (
    "flow",
    "spread",
    "basis_funding",
    "open_interest",
    "liquidations",
)
EVENT_TYPES = (
    "agg_trade",
    "book_ticker",
    "mark_price",
    "open_interest",
    "liquidation",
)

Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=180,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,179}$",
    ),
]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
FeatureScalar = float | int | str | None
FeatureVector = dict[str, FeatureScalar]

_FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "pnl",
        "net_pnl",
        "gross_pnl",
        "realized_pnl",
        "future_return",
        "future_ret",
        "future_outcome",
        "trade_outcome",
        "outcome",
        "exit_reason",
        "exit_price",
        "close_price",
        "close_time",
        "close_time_utc",
        "mfe",
        "mae",
        "label",
        "target",
    }
)
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "authorization",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)


class FreshnessStatus(str, Enum):
    FRESH = "FRESH"
    STALE = "STALE"
    MISSING = "MISSING"
    INVALID = "INVALID"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"


class MarketIntelligenceStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"


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


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    raise TypeError(f"unsupported_json_type:{type(value).__name__}")


def _walk_keys(value: Any) -> list[str]:
    keys: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            keys.append(str(key).casefold())
            keys.extend(_walk_keys(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            keys.extend(_walk_keys(child))
    return keys


class MarketEvent(FrozenContract):
    schema_version: Literal["market_intelligence_event_v1"] = EVENT_SCHEMA_VERSION
    event_id: Identifier
    source_id: Identifier
    exchange: Identifier
    symbol: Identifier
    event_type: Literal[
        "agg_trade",
        "book_ticker",
        "mark_price",
        "open_interest",
        "liquidation",
    ]
    event_time_utc: datetime
    received_at_utc: datetime
    available_at_utc: datetime
    processed_at_utc: datetime | None = None
    source_sequence: int | None = Field(default=None, ge=0)
    source_hash: Sha256Hex | None = None
    event_hash: Sha256Hex | None = None
    payload: dict[str, Any]

    @field_validator(
        "event_time_utc",
        "received_at_utc",
        "available_at_utc",
        "processed_at_utc",
    )
    @classmethod
    def _validate_timestamp(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)

    @model_validator(mode="after")
    def _validate_temporal_and_payload_contract(self) -> "MarketEvent":
        if self.event_time_utc > self.received_at_utc:
            raise ValueError("event_time_after_received_at")
        if self.received_at_utc > self.available_at_utc:
            raise ValueError("received_at_after_available_at")
        if self.processed_at_utc is not None and self.processed_at_utc < self.received_at_utc:
            raise ValueError("processed_at_before_received_at")
        if self.source_hash is None and self.event_hash is None:
            raise ValueError("source_hash_or_event_hash_required")
        keys = _walk_keys(self.payload)
        forbidden = sorted(
            key
            for key in keys
            if key in _FORBIDDEN_PAYLOAD_KEYS
            or key.startswith("future_ret_")
            or key.startswith("target_")
            or key.startswith("label_")
        )
        if forbidden:
            raise ValueError(f"outcome_or_future_field_forbidden:{','.join(forbidden)}")
        sensitive = sorted(
            key for key in keys if any(part in key for part in _SENSITIVE_KEY_PARTS)
        )
        if sensitive:
            raise ValueError(f"sensitive_field_forbidden:{','.join(sensitive)}")
        return self

    def effective_hash(self) -> str:
        if self.event_hash is not None:
            return self.event_hash
        if self.source_hash is not None:
            return canonical_sha256(
                {
                    "source_hash": self.source_hash,
                    "source_sequence": self.source_sequence,
                    "event_id": self.event_id,
                    "event_type": self.event_type,
                    "event_time_utc": self.event_time_utc,
                    "available_at_utc": self.available_at_utc,
                    "payload": self.payload,
                }
            )
        raise ValueError("event_hash_unavailable")

    def point_in_time_errors(self, decision_time_utc: datetime) -> tuple[str, ...]:
        decision = require_utc(decision_time_utc)
        errors: list[str] = []
        if self.available_at_utc > decision:
            errors.append("available_at_utc_after_decision_time")
        if self.event_time_utc > decision:
            errors.append("event_time_utc_after_decision_time")
        if self.received_at_utc > decision:
            errors.append("received_at_utc_after_decision_time")
        return tuple(errors)


class FeatureDefinition(FrozenContract):
    feature_name: Identifier
    feature_family: Literal[
        "flow", "spread", "basis_funding", "open_interest", "liquidations"
    ]
    dtype: Literal["float", "int", "str"]
    unit: NonEmptyText
    window_seconds: int | None = Field(default=None, gt=0)
    source_type: NonEmptyText
    calculation_version: Identifier
    availability_rule: NonEmptyText
    nan_policy: NonEmptyText
    range_policy: NonEmptyText
    point_in_time_required: Literal[True] = True
    research_only: Literal[True] = True


class SourceWatermark(FrozenContract):
    source_id: Identifier
    exchange: Identifier
    symbol: Identifier
    event_type: Literal[
        "agg_trade",
        "book_ticker",
        "mark_price",
        "open_interest",
        "liquidation",
    ]
    min_event_time_utc: datetime
    max_event_time_utc: datetime
    min_available_at_utc: datetime
    max_available_at_utc: datetime
    row_count: int = Field(gt=0)
    source_hash: Sha256Hex
    loader_version: Identifier
    schema_version: Identifier

    @field_validator(
        "min_event_time_utc",
        "max_event_time_utc",
        "min_available_at_utc",
        "max_available_at_utc",
    )
    @classmethod
    def _validate_watermark_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class FeatureFamilyHealth(FrozenContract):
    family: Literal["flow", "spread", "basis_funding", "open_interest", "liquidations"]
    status: FreshnessStatus
    latest_event_time_utc: datetime | None = None
    latest_available_at_utc: datetime | None = None
    age_seconds: float | None = Field(default=None, ge=0)
    max_age_seconds: float = Field(gt=0)
    event_count: int = Field(ge=0)
    reason: str | None = None

    @field_validator("latest_event_time_utc", "latest_available_at_utc")
    @classmethod
    def _validate_health_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)


class MarketIntelligenceRequest(FrozenContract):
    schema_version: Literal["market_intelligence_request_v1"] = "market_intelligence_request_v1"
    request_id: Identifier
    exchange: Identifier
    symbol: Identifier
    decision_time_utc: datetime
    events: tuple[MarketEvent, ...]
    research_council_snapshot: dict[str, Any] | None = None

    @field_validator("decision_time_utc")
    @classmethod
    def _validate_decision_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _validate_alignment(self) -> "MarketIntelligenceRequest":
        if any(item.symbol != self.symbol for item in self.events):
            raise ValueError("event_symbol_mismatch")
        if any(item.exchange != self.exchange for item in self.events):
            raise ValueError("event_exchange_mismatch")
        return self

    def point_in_time_errors(self) -> tuple[str, ...]:
        errors: list[str] = []
        for event in self.events:
            errors.extend(
                f"{event.event_id}:{reason}"
                for reason in event.point_in_time_errors(self.decision_time_utc)
            )
        return tuple(errors)


class MarketIntelligenceConfig(FrozenContract):
    schema_version: Literal["market_intelligence_config_v1"] = CONFIG_SCHEMA_VERSION
    mode: Literal["research"] = "research"
    paper_only: Literal[True] = True
    shadow_only: Literal[True] = True
    research_only: Literal[True] = True
    operational_authority: Literal[False] = False
    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False
    changes_risk: Literal[False] = False
    changes_model: Literal[False] = False
    live_release_allowed: Literal[False] = False
    canary_release_allowed: Literal[False] = False
    writes_active_signals: Literal[False] = False
    network_required: Literal[False] = False
    feature_windows_seconds: tuple[int, ...] = (1, 5, 15, 60)
    freshness_thresholds_seconds: dict[str, float] = Field(
        default_factory=lambda: {
            "flow": 15.0,
            "spread": 15.0,
            "basis_funding": 300.0,
            "open_interest": 300.0,
            "liquidations": 60.0,
        }
    )
    enabled_feature_families: tuple[str, ...] = CORE_FEATURE_FAMILIES
    real_source_available: dict[str, bool] = Field(
        default_factory=lambda: {
            "flow": True,
            "spread": True,
            "basis_funding": False,
            "open_interest": False,
            "liquidations": False,
        }
    )
    allowed_public_sources: tuple[str, ...] = (
        "binance_usdm_futures_public",
        "binance_public_rest_book_ticker",
        "binance_usdm_futures_public_aggtrades",
        "offline_fixture",
    )
    spread_zscore_window_seconds: int = Field(default=60, gt=0)
    spread_zscore_min_observations: int = Field(default=5, ge=2)
    large_trade_quantile: float = Field(default=0.75, ge=0.5, le=0.99)
    funding_extremeness_min_observations: int = Field(default=5, ge=2)

    @model_validator(mode="after")
    def _validate_config(self) -> "MarketIntelligenceConfig":
        windows = tuple(self.feature_windows_seconds)
        if not windows or any(value <= 0 for value in windows):
            raise ValueError("feature_windows_seconds_invalid")
        if tuple(sorted(set(windows))) != windows:
            raise ValueError("feature_windows_seconds_must_be_sorted_unique")
        unknown = sorted(set(self.enabled_feature_families) - set(CORE_FEATURE_FAMILIES))
        if unknown:
            raise ValueError(f"unknown_feature_family:{','.join(unknown)}")
        for family in self.enabled_feature_families:
            threshold = self.freshness_thresholds_seconds.get(family)
            if threshold is None or threshold <= 0:
                raise ValueError(f"freshness_threshold_missing_or_invalid:{family}")
            if family not in self.real_source_available:
                raise ValueError(f"source_availability_missing:{family}")
        return self


class MarketIntelligenceSnapshot(FrozenContract):
    snapshot_id: Identifier
    schema_version: Literal["market_intelligence_snapshot_v1"] = SNAPSHOT_SCHEMA_VERSION
    status: Literal["SUCCESS", "PARTIAL"]
    reason: str | None = None
    exchange: Identifier
    symbol: Identifier
    decision_time_utc: datetime
    created_at_utc: datetime
    source_watermarks: tuple[SourceWatermark, ...]
    flow_features: FeatureVector | None = None
    spread_features: FeatureVector | None = None
    basis_funding_features: FeatureVector | None = None
    open_interest_features: FeatureVector | None = None
    liquidation_features: FeatureVector | None = None
    research_council_context: dict[str, Any] | None = None
    feature_family_statuses: dict[str, FeatureFamilyHealth]
    feature_manifest: tuple[FeatureDefinition, ...]
    coverage: float = Field(ge=0.0, le=1.0)
    available_feature_families: tuple[str, ...]
    missing_feature_families: tuple[str, ...]
    point_in_time_valid: Literal[True] = True
    paper_only: Literal[True] = True
    shadow_only: Literal[True] = True
    research_only: Literal[True] = True
    operational_authority: Literal[False] = False
    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False
    changes_risk: Literal[False] = False
    changes_model: Literal[False] = False
    writes_active_signals: Literal[False] = False

    @field_validator("decision_time_utc", "created_at_utc")
    @classmethod
    def _validate_snapshot_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class MarketIntelligenceRunReport(FrozenContract):
    status: MarketIntelligenceStatus
    reason: str
    request_id: str | None
    input_event_count: int = Field(ge=0)
    valid_point_in_time_event_count: int = Field(ge=0)
    invalid_point_in_time_event_count: int = Field(ge=0)
    snapshot: MarketIntelligenceSnapshot | None = None
    write_requested: bool = False
    write_performed: bool = False
    output_paths: dict[str, str] = Field(default_factory=dict)
    network_calls_executed: Literal[False] = False
    paper_only: Literal[True] = True
    shadow_only: Literal[True] = True
    research_only: Literal[True] = True
    operational_authority: Literal[False] = False
    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False
    changes_risk: Literal[False] = False
    changes_model: Literal[False] = False
    writes_active_signals: Literal[False] = False


class AblationVariant(FrozenContract):
    variant_id: Identifier
    feature_families: tuple[str, ...]
    feature_names: tuple[str, ...]
    feature_count: int = Field(ge=0)


class AblationManifest(FrozenContract):
    schema_version: Literal["market_intelligence_ablation_manifest_v1"] = (
        "market_intelligence_ablation_manifest_v1"
    )
    ablation_id: Identifier
    status: Literal["ABLATION_DATA_READY", "BLOCKED_LEAKAGE", "NO_AVAILABLE_FEATURES"]
    reason: str
    snapshot_id: Identifier
    baseline_feature_names: tuple[str, ...]
    variants: tuple[AblationVariant, ...]
    rejected_leakage_features: tuple[str, ...]
    deterministic: Literal[True] = True
    training_performed: Literal[False] = False
    model_promoted: Literal[False] = False
    registry_write_performed: Literal[False] = False
    paper_only: Literal[True] = True
    shadow_only: Literal[True] = True
    research_only: Literal[True] = True
    operational_authority: Literal[False] = False
    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False
    changes_risk: Literal[False] = False
    changes_model: Literal[False] = False
    writes_active_signals: Literal[False] = False
