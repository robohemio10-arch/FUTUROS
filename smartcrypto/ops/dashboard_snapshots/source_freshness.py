from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


class FreshnessBasis(str, Enum):
    PAYLOAD_TIMESTAMP = "PAYLOAD_TIMESTAMP"
    FILE_MTIME = "FILE_MTIME"
    PAYLOAD_TIMESTAMP_OR_FILE_MTIME = "PAYLOAD_TIMESTAMP_OR_FILE_MTIME"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class TimestampSource(str, Enum):
    PAYLOAD = "payload"
    FILE_MTIME = "file_mtime"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"


class SourceHealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"
    PLANNED = "PLANNED"
    UNKNOWN = "UNKNOWN"


class FreshnessStatus(str, Enum):
    FRESH = "FRESH"
    WARNING_STALE = "WARNING_STALE"
    CRITICAL_STALE = "CRITICAL_STALE"
    STALE = "STALE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


DEFAULT_TIMESTAMP_FIELDS = (
    "last_updated_utc",
    "generated_at_utc",
    "build_finished_utc",
    "build_started_utc",
    "timestamp_utc",
    "created_at_utc",
    "report_generated_utc",
    "market_features_max_timestamp",
    "input_data_timestamp",
    "max_timestamp",
    "latest_timestamp",
    "data_timestamp",
    "as_of_utc",
    "finished_utc",
    "updated_at_utc",
    "generated_at",
    "created_at",
)


@dataclass(frozen=True)
class FreshnessPolicy:
    freshness_required: bool
    freshness_basis: FreshnessBasis
    max_age_seconds: float | None = None
    warning_age_seconds: float | None = None
    critical_age_seconds: float | None = None
    timestamp_fields: tuple[str, ...] = DEFAULT_TIMESTAMP_FIELDS
    fallback_to_mtime: bool = False
    stale_behavior: str = "DEGRADE"
    missing_behavior: str = "DEGRADE"
    invalid_timestamp_behavior: str = "DEGRADE"
    timezone_normalization: str = "UTC"
    operator_hint: str = "Consult the source runbook."
    producer_hint: str = "Run the documented source producer."

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["freshness_basis"] = self.freshness_basis.value
        payload["timestamp_fields"] = list(self.timestamp_fields)
        return payload


@dataclass(frozen=True)
class FreshnessEvaluation:
    freshness_status: FreshnessStatus
    timestamp_source: TimestampSource
    effective_timestamp_utc: str | None
    file_mtime_utc: str | None
    age_seconds: float | None
    stale: bool
    invalid_timestamp: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["freshness_status"] = self.freshness_status.value
        payload["timestamp_source"] = self.timestamp_source.value
        return payload


def no_freshness_policy(
    *,
    stale_behavior: str = "DEGRADE",
    missing_behavior: str = "DEGRADE",
    operator_hint: str = "Consult the source runbook.",
    producer_hint: str = "Run the documented source producer.",
) -> FreshnessPolicy:
    return FreshnessPolicy(
        freshness_required=False,
        freshness_basis=FreshnessBasis.NOT_APPLICABLE,
        stale_behavior=stale_behavior,
        missing_behavior=missing_behavior,
        invalid_timestamp_behavior=stale_behavior,
        operator_hint=operator_hint,
        producer_hint=producer_hint,
    )


def policy_from_mapping(value: Mapping[str, Any] | None) -> FreshnessPolicy:
    if not value:
        return no_freshness_policy()
    raw_basis = str(value.get("freshness_basis") or value.get("basis") or "NOT_APPLICABLE")
    basis = FreshnessBasis(raw_basis.upper())
    max_age = optional_float(value.get("max_age_seconds"))
    warning_age = optional_float(value.get("warning_age_seconds"))
    critical_age = optional_float(value.get("critical_age_seconds"))
    if critical_age is None:
        critical_age = max_age
    fields = value.get("timestamp_fields") or DEFAULT_TIMESTAMP_FIELDS
    return FreshnessPolicy(
        freshness_required=bool(
            value.get("freshness_required", basis is not FreshnessBasis.NOT_APPLICABLE)
        ),
        freshness_basis=basis,
        max_age_seconds=max_age,
        warning_age_seconds=warning_age,
        critical_age_seconds=critical_age,
        timestamp_fields=tuple(str(field) for field in fields),
        fallback_to_mtime=bool(
            value.get(
                "fallback_to_mtime",
                basis is FreshnessBasis.PAYLOAD_TIMESTAMP_OR_FILE_MTIME,
            )
        ),
        stale_behavior=str(value.get("stale_behavior", "DEGRADE")),
        missing_behavior=str(value.get("missing_behavior", "DEGRADE")),
        invalid_timestamp_behavior=str(value.get("invalid_timestamp_behavior", "DEGRADE")),
        timezone_normalization=str(value.get("timezone_normalization", "UTC")),
        operator_hint=str(value.get("operator_hint", "Consult the source runbook.")),
        producer_hint=str(value.get("producer_hint", "Run the documented source producer.")),
    )


def evaluate_freshness(
    path: Path,
    payload: Any,
    policy: FreshnessPolicy,
    now_utc: datetime,
) -> FreshnessEvaluation:
    current = ensure_utc(now_utc)
    file_mtime = file_mtime_utc(path)
    if policy.freshness_basis is FreshnessBasis.NOT_APPLICABLE:
        return FreshnessEvaluation(
            freshness_status=FreshnessStatus.NOT_APPLICABLE,
            timestamp_source=TimestampSource.NOT_APPLICABLE,
            effective_timestamp_utc=None,
            file_mtime_utc=iso_utc(file_mtime),
            age_seconds=None,
            stale=False,
            invalid_timestamp=False,
            reason="freshness_not_applicable",
        )

    payload_timestamp, payload_field, invalid_field = timestamp_from_payload(
        payload,
        policy.timestamp_fields,
    )
    if invalid_field is not None:
        return FreshnessEvaluation(
            freshness_status=FreshnessStatus.UNKNOWN,
            timestamp_source=TimestampSource.UNAVAILABLE,
            effective_timestamp_utc=None,
            file_mtime_utc=iso_utc(file_mtime),
            age_seconds=None,
            stale=False,
            invalid_timestamp=True,
            reason=f"invalid_payload_timestamp:{invalid_field}",
        )

    effective: datetime | None = None
    source = TimestampSource.UNAVAILABLE
    reason = "freshness_timestamp_unavailable"
    if policy.freshness_basis in {
        FreshnessBasis.PAYLOAD_TIMESTAMP,
        FreshnessBasis.PAYLOAD_TIMESTAMP_OR_FILE_MTIME,
    } and payload_timestamp is not None:
        effective = payload_timestamp
        source = TimestampSource.PAYLOAD
        reason = f"payload_timestamp:{payload_field}"
    elif policy.freshness_basis is FreshnessBasis.FILE_MTIME:
        effective = file_mtime
        source = TimestampSource.FILE_MTIME if file_mtime else TimestampSource.UNAVAILABLE
        reason = "file_mtime_timestamp" if file_mtime else reason
    elif policy.fallback_to_mtime:
        effective = file_mtime
        source = TimestampSource.FILE_MTIME if file_mtime else TimestampSource.UNAVAILABLE
        reason = "file_mtime_fallback" if file_mtime else reason

    if effective is None:
        return FreshnessEvaluation(
            freshness_status=FreshnessStatus.UNKNOWN,
            timestamp_source=source,
            effective_timestamp_utc=None,
            file_mtime_utc=iso_utc(file_mtime),
            age_seconds=None,
            stale=False,
            invalid_timestamp=False,
            reason=reason,
        )

    age = max((current - effective).total_seconds(), 0.0)
    status = classify_age(age, policy)
    return FreshnessEvaluation(
        freshness_status=status,
        timestamp_source=source,
        effective_timestamp_utc=iso_utc(effective),
        file_mtime_utc=iso_utc(file_mtime),
        age_seconds=age,
        stale=status in {
            FreshnessStatus.WARNING_STALE,
            FreshnessStatus.CRITICAL_STALE,
            FreshnessStatus.STALE,
        },
        invalid_timestamp=False,
        reason=reason,
    )


def classify_age(age_seconds: float, policy: FreshnessPolicy) -> FreshnessStatus:
    critical = policy.critical_age_seconds or policy.max_age_seconds
    warning = policy.warning_age_seconds
    if critical is not None and age_seconds > critical:
        return FreshnessStatus.CRITICAL_STALE
    if warning is not None and age_seconds > warning:
        return FreshnessStatus.WARNING_STALE
    if policy.max_age_seconds is not None and age_seconds > policy.max_age_seconds:
        return FreshnessStatus.STALE
    return FreshnessStatus.FRESH


def timestamp_from_payload(
    payload: Any,
    timestamp_fields: tuple[str, ...],
) -> tuple[datetime | None, str | None, str | None]:
    if not isinstance(payload, Mapping):
        return None, None, None
    for field in timestamp_fields:
        if field not in payload:
            continue
        value = payload.get(field)
        if value is None or str(value).strip() == "":
            continue
        parsed = parse_timestamp(value)
        if parsed is None:
            return None, None, field
        return parsed, field, None
    return None, None, None


def parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ensure_utc(parsed)


def file_mtime_utc(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def ensure_utc(value: datetime) -> datetime:
    current = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return ensure_utc(value).isoformat().replace("+00:00", "Z")


def optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
