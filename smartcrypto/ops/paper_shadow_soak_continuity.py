from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION = "paper_shadow_soak_continuity_v1"
DEFAULT_OUTPUT_PATH = Path("data/reports/paper_shadow_soak_continuity_audit.json")
DEFAULT_DIAGNOSTIC_SOAK_DAYS = 7
DEFAULT_REQUIRED_SOAK_DAYS = 30
DEFAULT_MAX_WARNING_GAP_MINUTES = 60
DEFAULT_MAX_CRITICAL_GAP_MINUTES = 360

SAFE_TRUE_FLAGS = ("paper_only", "shadow_only")
SAFE_FALSE_FLAGS = (
    "live_trading_enabled",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "changes_risk",
    "changes_training_dataset",
    "writes_trades_master",
    "live_release_allowed",
)
SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "live_trading_enabled": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
    "changes_training_dataset": False,
    "writes_trades_master": False,
    "live_release_allowed": False,
}

EVIDENCE_PATHS: dict[str, str] = {
    "paper_shadow_soak_report": "data/reports/paper_shadow_soak_report.json",
    "paper_soak_report": "data/reports/paper_soak_report.json",
    "runtime_evidence_pack_v2": "data/reports/runtime_evidence_pack_v2.json",
    "readiness_snapshot_v2": "data/reports/readiness_snapshot_v2.json",
    "freqtrade_paper_db_authority_report": "data/reports/freqtrade_paper_db_authority_report.json",
    "freqtrade_paper_db_authority": "data/reports/freqtrade_paper_db_authority.json",
    "phase14_feedback_sync_summary": "data/reports/phase14_feedback_sync_summary.json",
    "daily_ai_shadow_update_summary": "data/reports/daily_ai_shadow_update_summary.json",
    "ai_shadow_filter_incremental_daily_summary": "data/reports/ai_shadow_filter_incremental_daily_summary.json",
    "ai_shadow_filter_decision_db_audit_summary": "data/reports/ai_shadow_filter_decision_db_audit_summary.json",
}

MINIMUM_CONTINUITY_EVIDENCE = (
    "paper_shadow_soak_report",
    "paper_soak_report",
    "runtime_evidence_pack_v2",
    "readiness_snapshot_v2",
)

TIMESTAMP_KEYS = (
    "generated_at",
    "created_at",
    "updated_at",
    "timestamp",
    "started_at",
    "finished_at",
    "last_activity_at",
    "last_activity_timestamp",
    "last_trade_timestamp",
    "last_closed_trade_timestamp",
    "market_features_max_timestamp",
    "input_data_timestamp",
    "max_timestamp",
    "min_timestamp",
)

START_KEYS = ("start", "started_at", "start_time", "from", "from_timestamp", "begin", "min_timestamp")
END_KEYS = ("end", "ended_at", "end_time", "to", "to_timestamp", "finish", "finished_at", "max_timestamp")


@dataclass(frozen=True)
class AuditResult:
    report: dict[str, Any]
    output_path: Path
    write_performed: bool


def audit_paper_shadow_soak_continuity(
    *,
    project_root: str | Path = ".",
    output: str | Path = DEFAULT_OUTPUT_PATH,
    required_soak_days: int = DEFAULT_REQUIRED_SOAK_DAYS,
    diagnostic_soak_days: int = DEFAULT_DIAGNOSTIC_SOAK_DAYS,
    max_warning_gap_minutes: int = DEFAULT_MAX_WARNING_GAP_MINUTES,
    max_critical_gap_minutes: int = DEFAULT_MAX_CRITICAL_GAP_MINUTES,
    no_write: bool = False,
    now: datetime | None = None,
) -> AuditResult:
    root = Path(project_root).resolve()
    generated_at_dt = ensure_aware_utc(now or datetime.now(timezone.utc))
    output_path = resolve_under_root(root, output)

    missing_evidence: list[str] = []
    invalid_evidence: list[dict[str, str]] = []
    warning_reasons: list[str] = []
    blocking_reasons: list[str] = []
    next_required_actions: list[str] = []
    evidence_sources: list[dict[str, Any]] = []
    evidence_payloads: dict[str, Mapping[str, Any]] = {}

    for evidence_name, relative_path in EVIDENCE_PATHS.items():
        path = root / relative_path
        if not path.exists():
            missing_evidence.append(evidence_name)
            continue
        try:
            payload = load_json_object(path)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            invalid_evidence.append(
                {
                    "name": evidence_name,
                    "path": relative_path,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        evidence_payloads[evidence_name] = payload
        evidence_sources.append(
            {
                "name": evidence_name,
                "path": relative_path,
                "status": payload.get("status"),
                "exists": True,
            }
        )

    safety_violations = collect_safety_violations(evidence_payloads)
    if safety_violations:
        blocking_reasons.extend(safety_violations)

    explicit_observed_days = extract_observed_days(evidence_payloads)
    intervals = collect_intervals(evidence_payloads)
    timestamps = collect_timestamps(evidence_payloads)

    interval_metrics = build_interval_metrics(
        intervals=intervals,
        timestamps=timestamps,
        now=generated_at_dt,
        fallback_days=explicit_observed_days,
        max_warning_gap_minutes=max_warning_gap_minutes,
        max_critical_gap_minutes=max_critical_gap_minutes,
    )

    observed_calendar_days = interval_metrics["observed_calendar_days"]
    observed_active_days = max(interval_metrics["observed_active_days"], explicit_observed_days or 0.0)
    diagnostic_soak_reached = observed_calendar_days >= float(diagnostic_soak_days)
    readiness_soak_reached = observed_calendar_days >= float(required_soak_days)
    critical_gap_count = int(interval_metrics["critical_gap_count"])
    warning_gap_count = int(interval_metrics["warning_gap_count"])
    max_gap_minutes = interval_metrics["max_gap_minutes"]

    minimum_evidence_present = any(name in evidence_payloads for name in MINIMUM_CONTINUITY_EVIDENCE)
    if not minimum_evidence_present:
        next_required_actions.append(
            "Gerar paper_shadow_soak_report, runtime_evidence_pack_v2 ou readiness_snapshot_v2 para estimar continuidade."
        )

    if invalid_evidence:
        warning_reasons.append("Uma ou mais evidências JSON estão inválidas e foram ignoradas.")

    if not readiness_soak_reached:
        blocking_reasons.append(
            f"required_soak_days_not_reached: observed_calendar_days={observed_calendar_days:.4f}, required_soak_days={required_soak_days}"
        )
        next_required_actions.append("Manter paper/shadow ativo até completar a janela canônica de 30 dias sem gaps críticos.")

    if critical_gap_count > 0:
        blocking_reasons.append(f"critical_gap_count_gt_zero: {critical_gap_count}")
        next_required_actions.append("Investigar gaps críticos no soak e reiniciar a contagem de evidência se necessário.")

    if max_gap_minutes is not None and max_gap_minutes > float(max_critical_gap_minutes):
        blocking_reasons.append(
            f"max_gap_minutes_exceeds_critical_threshold: {max_gap_minutes:.4f} > {max_critical_gap_minutes}"
        )

    readiness_payload = evidence_payloads.get("readiness_snapshot_v2")
    if isinstance(readiness_payload, Mapping):
        readiness_status = str(readiness_payload.get("status", "")).lower()
        if readiness_status == "blocked":
            blocking_reasons.append("readiness_snapshot_v2_status_blocked")
        if readiness_payload.get("live_release_allowed") is True:
            blocking_reasons.append("readiness_snapshot_v2_live_release_allowed_true")

    evidence_pack_payload = evidence_payloads.get("runtime_evidence_pack_v2")
    if isinstance(evidence_pack_payload, Mapping):
        if evidence_pack_payload.get("live_release_allowed") is True:
            blocking_reasons.append("runtime_evidence_pack_v2_live_release_allowed_true")
        snapshot = evidence_pack_payload.get("readiness_snapshot")
        if isinstance(snapshot, Mapping) and snapshot.get("live_release_allowed") is True:
            blocking_reasons.append("runtime_evidence_pack_v2_snapshot_live_release_allowed_true")

    if warning_gap_count > 0:
        warning_reasons.append(f"warning_gap_count_gt_zero: {warning_gap_count}")
    if missing_evidence:
        warning_reasons.append("Uma ou mais evidências opcionais estão ausentes.")
    if not intervals and not timestamps and explicit_observed_days is None:
        warning_reasons.append("Nenhum intervalo/timestamp explícito foi encontrado nas evidências disponíveis.")

    if not minimum_evidence_present:
        status = "evidence_missing"
    elif blocking_reasons:
        status = "blocked"
    elif warning_reasons:
        status = "degraded"
    else:
        status = "ok"

    continuity_approved = status == "ok" and readiness_soak_reached and critical_gap_count == 0

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(generated_at_dt),
        "project_root": str(root),
        "status": status,
        "diagnostic_soak_days": diagnostic_soak_days,
        "required_soak_days": required_soak_days,
        "observed_calendar_days": round(observed_calendar_days, 6),
        "observed_active_days": round(observed_active_days, 6),
        "readiness_soak_reached": readiness_soak_reached,
        "diagnostic_soak_reached": diagnostic_soak_reached,
        "continuity_approved": continuity_approved,
        "live_release_allowed": False,
        "covered_intervals": interval_metrics["covered_intervals"],
        "missing_intervals": interval_metrics["missing_intervals"],
        "max_gap_minutes": max_gap_minutes,
        "critical_gap_count": critical_gap_count,
        "warning_gap_count": warning_gap_count,
        "missing_evidence": sorted(missing_evidence),
        "invalid_evidence": invalid_evidence,
        "blocking_reasons": sorted(set(blocking_reasons)),
        "warning_reasons": sorted(set(warning_reasons)),
        "next_required_actions": sorted(set(next_required_actions)),
        "evidence_sources": evidence_sources,
        "safety_flags": dict(SAFETY_FLAGS),
    }

    write_performed = False
    if not no_write:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_performed = True

    return AuditResult(report=report, output_path=output_path, write_performed=write_performed)


def build_interval_metrics(
    *,
    intervals: Sequence[tuple[datetime, datetime]],
    timestamps: Sequence[datetime],
    now: datetime,
    fallback_days: float | None,
    max_warning_gap_minutes: int,
    max_critical_gap_minutes: int,
) -> dict[str, Any]:
    normalized_intervals = merge_intervals(intervals)
    sorted_timestamps = sorted(set(ensure_aware_utc(item) for item in timestamps))

    covered_intervals = [
        {"start": iso(start), "end": iso(end), "duration_minutes": round((end - start).total_seconds() / 60.0, 6)}
        for start, end in normalized_intervals
    ]

    missing_intervals: list[dict[str, Any]] = []
    max_gap_minutes: float | None = None
    warning_gap_count = 0
    critical_gap_count = 0

    if len(sorted_timestamps) >= 2:
        for previous, current in zip(sorted_timestamps, sorted_timestamps[1:]):
            gap_minutes = (current - previous).total_seconds() / 60.0
            if max_gap_minutes is None or gap_minutes > max_gap_minutes:
                max_gap_minutes = gap_minutes
            if gap_minutes > float(max_warning_gap_minutes):
                severity = "critical" if gap_minutes > float(max_critical_gap_minutes) else "warning"
                if severity == "critical":
                    critical_gap_count += 1
                else:
                    warning_gap_count += 1
                missing_intervals.append(
                    {
                        "start": iso(previous),
                        "end": iso(current),
                        "duration_minutes": round(gap_minutes, 6),
                        "severity": severity,
                    }
                )

    if normalized_intervals:
        earliest = min(start for start, _ in normalized_intervals)
        latest = max(end for _, end in normalized_intervals)
        observed_calendar_days = max(0.0, (latest - earliest).total_seconds() / 86400.0)
        observed_active_days = sum((end - start).total_seconds() for start, end in normalized_intervals) / 86400.0
    elif sorted_timestamps:
        earliest = sorted_timestamps[0]
        latest = sorted_timestamps[-1]
        observed_calendar_days = max(0.0, (latest - earliest).total_seconds() / 86400.0)
        observed_active_days = observed_calendar_days
    elif fallback_days is not None:
        observed_calendar_days = float(fallback_days)
        observed_active_days = float(fallback_days)
    else:
        observed_calendar_days = 0.0
        observed_active_days = 0.0

    if fallback_days is not None:
        observed_calendar_days = max(observed_calendar_days, float(fallback_days))
        observed_active_days = max(observed_active_days, float(fallback_days))

    return {
        "covered_intervals": covered_intervals,
        "missing_intervals": missing_intervals,
        "max_gap_minutes": None if max_gap_minutes is None else round(max_gap_minutes, 6),
        "critical_gap_count": critical_gap_count,
        "warning_gap_count": warning_gap_count,
        "observed_calendar_days": observed_calendar_days,
        "observed_active_days": observed_active_days,
    }


def load_json_object(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def resolve_under_root(root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return (root / candidate).resolve()


def iso(value: datetime) -> str:
    return ensure_aware_utc(value).isoformat().replace("+00:00", "Z")


def ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
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


def extract_observed_days(payloads: Mapping[str, Mapping[str, Any]]) -> float | None:
    keys = (
        "observed_soak_days",
        "observed_calendar_days",
        "observed_active_days",
        "paper_soak_days",
        "soak_days",
        "continuous_soak_days",
        "runtime_days",
    )
    observed: float | None = None
    for payload in payloads.values():
        for key in keys:
            value = parse_float(payload.get(key))
            if value is not None:
                observed = max(observed or 0.0, value)
        nested = payload.get("readiness_snapshot")
        if isinstance(nested, Mapping):
            nested_days = extract_observed_days({"nested": nested})
            if nested_days is not None:
                observed = max(observed or 0.0, nested_days)
    return observed


def collect_timestamps(payloads: Mapping[str, Mapping[str, Any]]) -> list[datetime]:
    timestamps: list[datetime] = []
    for payload in payloads.values():
        timestamps.extend(iter_timestamps(payload))
    return timestamps


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
