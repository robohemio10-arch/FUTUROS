from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .catalog import ANCHOR_FAMILY_NAMES, iter_soak_gap_accounting_sources
from .contracts import (
    CRITICAL_GAP_KEYS,
    DASHBOARD_NAME,
    DEFAULT_DIAGNOSTIC_SOAK_DAYS,
    DEFAULT_MAX_CRITICAL_GAP_MINUTES,
    DEFAULT_MAX_WARNING_GAP_MINUTES,
    DEFAULT_OUTPUT_PATH,
    DEFAULT_REQUIRED_SOAK_DAYS,
    END_KEYS,
    GapAccountingResult,
    MAX_GAP_KEYS,
    OBSERVED_SOAK_KEYS,
    PROJECT_NAME,
    SAFE_FALSE_FLAGS,
    SAFE_TRUE_FLAGS,
    SCHEMA_VERSION,
    START_KEYS,
    STATUS_KEYS,
    TIMESTAMP_KEYS,
    WARNING_GAP_KEYS,
)


def audit_paper_shadow_soak_continuity_and_gap_accounting(
    *,
    project_root: str | Path = ".",
    output: str | Path = DEFAULT_OUTPUT_PATH,
    diagnostic_soak_days: int = DEFAULT_DIAGNOSTIC_SOAK_DAYS,
    required_soak_days: int = DEFAULT_REQUIRED_SOAK_DAYS,
    max_warning_gap_minutes: int = DEFAULT_MAX_WARNING_GAP_MINUTES,
    max_critical_gap_minutes: int = DEFAULT_MAX_CRITICAL_GAP_MINUTES,
    write: bool = False,
    now: datetime | None = None,
) -> GapAccountingResult:
    root = Path(project_root).resolve()
    generated_at_dt = ensure_aware_utc(now or datetime.now(timezone.utc))
    output_path = resolve_under_root(root, output)

    evidence_payloads: dict[str, Mapping[str, Any]] = {}
    evidence_sources: list[dict[str, Any]] = []
    invalid_evidence: list[dict[str, str]] = []

    for source in iter_soak_gap_accounting_sources():
        source_path = root / source.path
        record: dict[str, Any] = {
            "name": source.name,
            "path": source.path,
            "exists": source_path.exists(),
            "required_for_accounting": source.required_for_accounting,
            "required_for_readiness": source.required_for_readiness,
            "status": None,
        }
        if source_path.exists() and source_path.suffix.lower() == ".json":
            try:
                payload = load_json_object(source_path)
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                invalid_evidence.append(
                    {"name": source.name, "path": source.path, "error": f"{type(exc).__name__}: {exc}"}
                )
                record["status"] = "invalid"
            else:
                evidence_payloads[source.name] = payload
                record["status"] = first_string(payload, STATUS_KEYS)
        elif source_path.exists():
            record["status"] = "versioned_file_present"
        evidence_sources.append(record)

    anchor_family_present = any(
        record["name"] in ANCHOR_FAMILY_NAMES and record["exists"] and record["status"] != "invalid"
        for record in evidence_sources
    )
    missing_required_accounting_evidence = sorted(
        source.name
        for source in iter_soak_gap_accounting_sources()
        if source.required_for_accounting and not (root / source.path).exists()
    )
    missing_required_readiness_evidence = sorted(
        source.name
        for source in iter_soak_gap_accounting_sources()
        if source.required_for_readiness and not (root / source.path).exists()
    )

    timestamps = collect_timestamps(evidence_payloads)
    intervals = collect_intervals(evidence_payloads)
    explicit_missing_windows = collect_explicit_missing_windows(evidence_payloads)
    explicit_observed_days = max_float(find_numeric_values(evidence_payloads.values(), OBSERVED_SOAK_KEYS))
    explicit_critical_gap_count = max_int(find_numeric_values(evidence_payloads.values(), CRITICAL_GAP_KEYS), default=0)
    explicit_warning_gap_count = max_int(find_numeric_values(evidence_payloads.values(), WARNING_GAP_KEYS), default=0)
    explicit_max_gap_minutes = max_float(find_numeric_values(evidence_payloads.values(), MAX_GAP_KEYS))

    timeline = build_gap_timeline(
        intervals=intervals,
        timestamps=timestamps,
        explicit_missing_windows=explicit_missing_windows,
        explicit_observed_days=explicit_observed_days,
        explicit_critical_gap_count=explicit_critical_gap_count,
        explicit_warning_gap_count=explicit_warning_gap_count,
        explicit_max_gap_minutes=explicit_max_gap_minutes,
        max_warning_gap_minutes=max_warning_gap_minutes,
        max_critical_gap_minutes=max_critical_gap_minutes,
        now=generated_at_dt,
    )

    observed_calendar_days = timeline["observed_calendar_days"]
    observed_active_days = timeline["observed_active_days"]
    continuous_valid_soak_days = timeline["continuous_valid_soak_days"]
    critical_gap_count = int(timeline["critical_gap_count"])
    warning_gap_count = int(timeline["warning_gap_count"])
    max_gap_minutes = timeline["max_gap_minutes"]

    safety_violations = collect_safety_violations(evidence_payloads)
    blocking_reasons: list[str] = []
    warning_reasons: list[str] = []
    blocking_reasons.extend(safety_violations)
    if invalid_evidence:
        blocking_reasons.append("invalid_json_evidence_present")
    if not anchor_family_present:
        blocking_reasons.append("missing_soak_anchor_family_evidence")
    if observed_calendar_days < float(required_soak_days):
        blocking_reasons.append(
            f"required_soak_days_not_reached: observed_calendar_days={observed_calendar_days:.4f}, required_soak_days={required_soak_days}"
        )
    if continuous_valid_soak_days < float(required_soak_days):
        blocking_reasons.append(
            f"continuous_valid_soak_days_below_required: continuous_valid_soak_days={continuous_valid_soak_days:.4f}, required_soak_days={required_soak_days}"
        )
    if critical_gap_count > 0:
        blocking_reasons.append(f"critical_gap_count_gt_zero: {critical_gap_count}")
    if max_gap_minutes is not None and max_gap_minutes > float(max_critical_gap_minutes):
        blocking_reasons.append(
            f"max_gap_minutes_exceeds_critical_threshold: {max_gap_minutes:.4f} > {max_critical_gap_minutes}"
        )

    blocked_source_names = sorted(
        record["name"]
        for record in evidence_sources
        if str(record.get("status") or "").lower() == "blocked" and record["required_for_readiness"]
    )
    if blocked_source_names:
        blocking_reasons.append("required_readiness_sources_blocked: " + ",".join(blocked_source_names))

    if missing_required_readiness_evidence:
        warning_reasons.append("missing_required_readiness_evidence")
    if warning_gap_count > 0:
        warning_reasons.append(f"warning_gap_count_gt_zero: {warning_gap_count}")
    if not timestamps and not intervals and explicit_observed_days is None:
        warning_reasons.append("no_timeline_evidence_found")

    diagnostic_soak_reached = observed_calendar_days >= float(diagnostic_soak_days)
    readiness_soak_reached = observed_calendar_days >= float(required_soak_days)
    continuity_accounting_established = anchor_family_present and not invalid_evidence
    readiness_gap_free = critical_gap_count == 0 and continuous_valid_soak_days >= float(required_soak_days)

    if not continuity_accounting_established:
        status = "evidence_missing"
        reason = "missing_or_invalid_soak_continuity_evidence"
    elif blocking_reasons:
        status = "blocked"
        reason = ";".join(sorted(set(blocking_reasons)))
    elif warning_reasons:
        status = "degraded"
        reason = ";".join(sorted(set(warning_reasons)))
    else:
        status = "ok"
        reason = "paper_shadow_soak_gap_accounting_current"

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "project_name": PROJECT_NAME,
        "dashboard_name": DASHBOARD_NAME,
        "generated_at_utc": iso(generated_at_dt),
        "status": status,
        "reason": reason,
        "diagnostic_soak_days": int(diagnostic_soak_days),
        "required_soak_days": int(required_soak_days),
        "observed_calendar_days": round(observed_calendar_days, 6),
        "observed_active_days": round(observed_active_days, 6),
        "continuous_valid_soak_days": round(continuous_valid_soak_days, 6),
        "diagnostic_soak_reached": diagnostic_soak_reached,
        "readiness_soak_reached": readiness_soak_reached,
        "readiness_gap_free": readiness_gap_free,
        "seven_day_diagnostic_status": gate_status(
            reached=diagnostic_soak_reached,
            blocked=not continuity_accounting_established,
            known=anchor_family_present,
        ),
        "thirty_day_readiness_status": gate_status(
            reached=readiness_soak_reached and readiness_gap_free and not blocking_reasons,
            blocked=bool(blocking_reasons),
            known=anchor_family_present,
        ),
        "effective_soak_start_utc": timeline["effective_soak_start_utc"],
        "effective_soak_end_utc": timeline["effective_soak_end_utc"],
        "covered_intervals": timeline["covered_intervals"],
        "gap_windows": timeline["gap_windows"],
        "hourly_coverage": timeline["hourly_coverage"],
        "daily_coverage": timeline["daily_coverage"],
        "max_gap_minutes": max_gap_minutes,
        "critical_gap_count": critical_gap_count,
        "warning_gap_count": warning_gap_count,
        "missing_required_accounting_evidence": missing_required_accounting_evidence,
        "missing_required_readiness_evidence": missing_required_readiness_evidence,
        "invalid_evidence": invalid_evidence,
        "blocking_reasons": sorted(set(blocking_reasons)),
        "warning_reasons": sorted(set(warning_reasons)),
        "next_required_actions": next_required_actions(
            observed_calendar_days=observed_calendar_days,
            continuous_valid_soak_days=continuous_valid_soak_days,
            required_soak_days=required_soak_days,
            critical_gap_count=critical_gap_count,
            missing_required_readiness_evidence=missing_required_readiness_evidence,
        ),
        "evidence_sources": evidence_sources,
        "continuity_accounting_established": continuity_accounting_established,
        "manual_go_no_go_required": True,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "sends_notifications": False,
        "changes_risk": False,
        "changes_model": False,
        "changes_config": False,
        "changes_active_signals": False,
        "changes_readiness": False,
        "runs_ocr": False,
        "imports_trades": False,
        "rebuilds_dataset": False,
        "cleans_sqlite": False,
        "writes_trades_master": False,
        "safety": build_safety_block(),
        "write_performed": False,
    }

    write_performed = False
    if write:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        report_to_write = dict(report)
        report_to_write["write_performed"] = True
        output_path.write_text(
            json.dumps(report_to_write, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        write_performed = True
        report["write_performed"] = True

    return GapAccountingResult(report=report, output_path=output_path, write_performed=write_performed)


def build_gap_timeline(
    *,
    intervals: Sequence[tuple[datetime, datetime]],
    timestamps: Sequence[datetime],
    explicit_missing_windows: Sequence[dict[str, Any]],
    explicit_observed_days: float | None,
    explicit_critical_gap_count: int,
    explicit_warning_gap_count: int,
    explicit_max_gap_minutes: float | None,
    max_warning_gap_minutes: int,
    max_critical_gap_minutes: int,
    now: datetime,
) -> dict[str, Any]:
    normalized_intervals = merge_intervals(intervals)
    sorted_timestamps = sorted(set(ensure_aware_utc(item) for item in timestamps))
    covered_intervals = [
        {"start_utc": iso(start), "end_utc": iso(end), "duration_minutes": round(delta_minutes(start, end), 6)}
        for start, end in normalized_intervals
    ]

    gap_windows: list[dict[str, Any]] = []
    for previous, current in zip(sorted_timestamps, sorted_timestamps[1:]):
        minutes = delta_minutes(previous, current)
        if minutes > float(max_warning_gap_minutes):
            severity = "critical" if minutes > float(max_critical_gap_minutes) else "warning"
            gap_windows.append(
                {
                    "start_utc": iso(previous),
                    "end_utc": iso(current),
                    "duration_minutes": round(minutes, 6),
                    "severity": severity,
                    "source": "timestamp_gap",
                }
            )

    for item in explicit_missing_windows:
        gap_windows.append(item)

    if normalized_intervals:
        earliest = min(start for start, _ in normalized_intervals)
        latest = max(end for _, end in normalized_intervals)
        observed_calendar_days = max(0.0, (latest - earliest).total_seconds() / 86400.0)
        observed_active_days = sum(max(0.0, (end - start).total_seconds()) for start, end in normalized_intervals) / 86400.0
    elif sorted_timestamps:
        earliest = sorted_timestamps[0]
        latest = sorted_timestamps[-1]
        observed_calendar_days = max(0.0, (latest - earliest).total_seconds() / 86400.0)
        observed_active_days = observed_calendar_days
    else:
        latest = now
        earliest = now
        observed_calendar_days = 0.0
        observed_active_days = 0.0

    if explicit_observed_days is not None:
        observed_calendar_days = max(observed_calendar_days, explicit_observed_days)
        observed_active_days = max(observed_active_days, explicit_observed_days)
        if not normalized_intervals and not sorted_timestamps:
            earliest = now
            latest = now

    max_gap_minutes = max_float([item.get("duration_minutes") for item in gap_windows])
    if explicit_max_gap_minutes is not None:
        max_gap_minutes = max(explicit_max_gap_minutes, max_gap_minutes or 0.0)

    computed_critical_count = sum(1 for item in gap_windows if item.get("severity") == "critical")
    computed_warning_count = sum(1 for item in gap_windows if item.get("severity") == "warning")
    critical_gap_count = max(computed_critical_count, int(explicit_critical_gap_count))
    warning_gap_count = max(computed_warning_count, int(explicit_warning_gap_count))

    continuous_valid_soak_days = compute_continuous_valid_days(
        earliest=earliest,
        latest=latest,
        gap_windows=gap_windows,
        fallback_days=explicit_observed_days,
    )
    daily_coverage = build_daily_coverage(normalized_intervals, sorted_timestamps, explicit_observed_days)
    hourly_coverage = build_hourly_coverage(normalized_intervals, sorted_timestamps)

    return {
        "covered_intervals": covered_intervals,
        "gap_windows": sorted(gap_windows, key=lambda item: (item.get("start_utc") or "", item.get("end_utc") or "")),
        "effective_soak_start_utc": iso(earliest) if observed_calendar_days > 0 or observed_active_days > 0 else None,
        "effective_soak_end_utc": iso(latest) if observed_calendar_days > 0 or observed_active_days > 0 else None,
        "observed_calendar_days": observed_calendar_days,
        "observed_active_days": observed_active_days,
        "continuous_valid_soak_days": continuous_valid_soak_days,
        "critical_gap_count": critical_gap_count,
        "warning_gap_count": warning_gap_count,
        "max_gap_minutes": None if max_gap_minutes is None else round(max_gap_minutes, 6),
        "hourly_coverage": hourly_coverage,
        "daily_coverage": daily_coverage,
    }


def compute_continuous_valid_days(
    *, earliest: datetime, latest: datetime, gap_windows: Sequence[Mapping[str, Any]], fallback_days: float | None
) -> float:
    critical_gaps: list[tuple[datetime, datetime]] = []
    for item in gap_windows:
        if item.get("severity") != "critical":
            continue
        start = parse_timestamp(item.get("start_utc"))
        end = parse_timestamp(item.get("end_utc"))
        if start is not None and end is not None and end >= start:
            critical_gaps.append((start, end))
    if latest <= earliest and fallback_days is not None:
        return float(fallback_days) if not critical_gaps else 0.0
    if not critical_gaps:
        span_days = max(0.0, (latest - earliest).total_seconds() / 86400.0)
        return max(span_days, float(fallback_days or 0.0))
    boundaries = [earliest]
    for start, end in sorted(critical_gaps):
        boundaries.append(start)
        boundaries.append(end)
    boundaries.append(latest)
    longest_seconds = 0.0
    current_start = earliest
    for start, end in sorted(critical_gaps):
        longest_seconds = max(longest_seconds, max(0.0, (start - current_start).total_seconds()))
        current_start = max(current_start, end)
    longest_seconds = max(longest_seconds, max(0.0, (latest - current_start).total_seconds()))
    return longest_seconds / 86400.0


def build_daily_coverage(
    intervals: Sequence[tuple[datetime, datetime]], timestamps: Sequence[datetime], fallback_days: float | None) -> list[dict[str, Any]]:
    coverage: dict[str, float] = defaultdict(float)
    for start, end in intervals:
        day = start.date()
        while datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) < end:
            day_start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
            day_end = day_start.replace(hour=23, minute=59, second=59, microsecond=999999)
            minutes = max(0.0, (min(end, day_end) - max(start, day_start)).total_seconds() / 60.0)
            if minutes > 0:
                coverage[day.isoformat()] += minutes
            day = (day_start.replace(hour=12).date()).fromordinal(day.toordinal() + 1)
    for stamp in timestamps:
        coverage.setdefault(stamp.date().isoformat(), 0.0)
    if not coverage and fallback_days:
        return [{"date": "explicit_observed_days", "covered_minutes": round(float(fallback_days) * 1440.0, 6), "coverage_ratio": 1.0}]
    return [
        {"date": day, "covered_minutes": round(minutes, 6), "coverage_ratio": round(min(1.0, minutes / 1440.0), 6)}
        for day, minutes in sorted(coverage.items())
    ]


def build_hourly_coverage(intervals: Sequence[tuple[datetime, datetime]], timestamps: Sequence[datetime]) -> list[dict[str, Any]]:
    coverage: dict[str, int] = defaultdict(int)
    for start, end in intervals:
        cursor = start.replace(minute=0, second=0, microsecond=0)
        while cursor <= end:
            coverage[iso(cursor)] += 1
            cursor = cursor.replace(hour=cursor.hour + 1) if cursor.hour < 23 else (cursor.replace(hour=0) + timedelta_days(1))
    for stamp in timestamps:
        coverage[iso(stamp.replace(minute=0, second=0, microsecond=0))] += 1
    return [{"hour_utc": hour, "sample_count": count} for hour, count in sorted(coverage.items())]


def collect_explicit_missing_windows(payloads: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    for source_name, payload in payloads.items():
        for key in ("missing_intervals", "gap_windows", "gaps", "soak_gaps"):
            value = payload.get(key)
            if isinstance(value, list):
                for item in value:
                    if not isinstance(item, Mapping):
                        continue
                    start = parse_timestamp(item.get("start") or item.get("start_utc"))
                    end = parse_timestamp(item.get("end") or item.get("end_utc"))
                    duration = parse_float(item.get("duration_minutes"))
                    if start is not None and end is not None:
                        duration = duration if duration is not None else delta_minutes(start, end)
                    if duration is None:
                        continue
                    severity = str(item.get("severity") or "warning").lower()
                    if severity not in {"warning", "critical"}:
                        severity = "warning"
                    windows.append(
                        {
                            "start_utc": iso(start) if start is not None else None,
                            "end_utc": iso(end) if end is not None else None,
                            "duration_minutes": round(float(duration), 6),
                            "severity": severity,
                            "source": source_name,
                        }
                    )
    return windows


def collect_timestamps(payloads: Mapping[str, Mapping[str, Any]]) -> list[datetime]:
    stamps: list[datetime] = []
    for payload in payloads.values():
        stamps.extend(iter_timestamps(payload))
    return stamps


def iter_timestamps(value: Any) -> Iterable[datetime]:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key) in TIMESTAMP_KEYS:
                parsed = parse_timestamp(nested)
                if parsed is not None:
                    yield parsed
            if isinstance(nested, Mapping | list | tuple):
                yield from iter_timestamps(nested)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from iter_timestamps(item)


def collect_intervals(payloads: Mapping[str, Mapping[str, Any]]) -> list[tuple[datetime, datetime]]:
    intervals: list[tuple[datetime, datetime]] = []
    for payload in payloads.values():
        intervals.extend(iter_intervals(payload))
    return intervals


def iter_intervals(value: Any) -> Iterable[tuple[datetime, datetime]]:
    if isinstance(value, Mapping):
        start = first_timestamp_for_keys(value, START_KEYS)
        end = first_timestamp_for_keys(value, END_KEYS)
        if start is not None and end is not None and end >= start:
            yield (start, end)
        for key in ("covered_intervals", "intervals", "active_intervals", "soak_intervals"):
            nested = value.get(key)
            if isinstance(nested, list):
                for item in nested:
                    yield from iter_intervals(item)
        for nested in value.values():
            if isinstance(nested, Mapping):
                yield from iter_intervals(nested)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from iter_intervals(item)


def first_timestamp_for_keys(payload: Mapping[str, Any], keys: Sequence[str]) -> datetime | None:
    for key in keys:
        parsed = parse_timestamp(payload.get(key))
        if parsed is not None:
            return parsed
    return None


def merge_intervals(intervals: Sequence[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    ordered = sorted((ensure_aware_utc(start), ensure_aware_utc(end)) for start, end in intervals if end >= start)
    if not ordered:
        return []
    merged: list[tuple[datetime, datetime]] = [ordered[0]]
    for start, end in ordered[1:]:
        previous_start, previous_end = merged[-1]
        if start <= previous_end:
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return merged


def collect_safety_violations(payloads: Mapping[str, Mapping[str, Any]]) -> list[str]:
    violations: list[str] = []
    for name, payload in payloads.items():
        for key, value in iter_key_values(payload):
            if key in SAFE_TRUE_FLAGS and value is False:
                violations.append(f"{name}:{key}=false")
            if key in SAFE_FALSE_FLAGS and value is True:
                violations.append(f"{name}:{key}=true")
    return sorted(set(violations))


def iter_key_values(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            yield str(key), nested
            if isinstance(nested, Mapping | list | tuple):
                yield from iter_key_values(nested)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from iter_key_values(item)


def find_numeric_values(payloads: Iterable[Any], keys: Sequence[str]) -> list[float]:
    values: list[float] = []
    for payload in payloads:
        if isinstance(payload, Mapping):
            for key, nested in payload.items():
                if str(key) in keys:
                    parsed = parse_float(nested)
                    if parsed is not None:
                        values.append(parsed)
                if isinstance(nested, Mapping | list | tuple):
                    values.extend(find_numeric_values([nested], keys))
        elif isinstance(payload, list | tuple):
            values.extend(find_numeric_values(payload, keys))
    return values


def first_string(payload: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return None


def max_float(values: Iterable[Any]) -> float | None:
    parsed = [item for item in (parse_float(value) for value in values) if item is not None]
    return max(parsed) if parsed else None


def max_int(values: Iterable[Any], *, default: int = 0) -> int:
    parsed = [int(item) for item in (parse_float(value) for value in values) if item is not None]
    return max(parsed) if parsed else default


def parse_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return ensure_aware_utc(value)
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        return ensure_aware_utc(datetime.fromisoformat(candidate))
    except ValueError:
        return None


def load_json_object(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def resolve_under_root(root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"Output path must remain under project root: {resolved}")
    return resolved


def iso(value: datetime) -> str:
    return ensure_aware_utc(value).isoformat().replace("+00:00", "Z")


def ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def delta_minutes(start: datetime, end: datetime) -> float:
    return (ensure_aware_utc(end) - ensure_aware_utc(start)).total_seconds() / 60.0


def timedelta_days(days: int):
    from datetime import timedelta

    return timedelta(days=days)


def gate_status(*, reached: bool, blocked: bool, known: bool) -> str:
    if not known:
        return "unknown"
    if blocked:
        return "blocked"
    return "reached" if reached else "not_reached"


def build_safety_block() -> dict[str, bool]:
    safety = {**SAFE_TRUE_FLAGS, **SAFE_FALSE_FLAGS}
    safety["dashboard_readonly"] = True
    safety["live_locked"] = True
    return safety


def next_required_actions(
    *,
    observed_calendar_days: float,
    continuous_valid_soak_days: float,
    required_soak_days: int,
    critical_gap_count: int,
    missing_required_readiness_evidence: Sequence[str],
) -> list[str]:
    actions: list[str] = []
    if observed_calendar_days < float(required_soak_days):
        remaining = max(0.0, float(required_soak_days) - observed_calendar_days)
        actions.append(f"Manter soak paper/shadow até completar 30 dias canônicos; faltam {remaining:.2f} dias.")
    if continuous_valid_soak_days < float(required_soak_days):
        actions.append("Manter uma janela contínua gap-free suficiente antes de qualquer readiness/canary review.")
    if critical_gap_count > 0:
        actions.append("Investigar gaps críticos e registrar causa antes de qualquer revisão de readiness.")
    if missing_required_readiness_evidence:
        actions.append("Gerar evidências obrigatórias de readiness ausentes antes de qualquer go/no-go manual.")
    if not actions:
        actions.append("Manter manual go/no-go obrigatório; nenhum release automático é permitido.")
    return sorted(set(actions))
