from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from smartcrypto.ops.paper_shadow_soak_anchor.catalog import (
    ANCHOR_FAMILY_NAMES,
    iter_soak_evidence_sources,
)
from smartcrypto.ops.paper_shadow_soak_anchor.contracts import (
    DEFAULT_DASHBOARD_NAME,
    DEFAULT_DIAGNOSTIC_SOAK_DAYS,
    DEFAULT_OUTPUT_PATH,
    DEFAULT_REQUIRED_SOAK_DAYS,
    PROJECT_NAME,
    SCHEMA_VERSION,
    SoakAnchorAuditResult,
)

UNSAFE_TRUE_FLAGS = (
    "live_trading_enabled",
    "live_release_allowed",
    "canary_release_allowed",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "sends_notifications",
    "changes_risk",
    "changes_model",
    "changes_config",
    "changes_active_signals",
    "changes_readiness",
    "changes_training_dataset",
    "writes_trades_master",
    "runs_ocr",
    "imports_trades",
    "rebuilds_dataset",
    "cleans_sqlite",
)

SAFE_TRUE_FLAGS = ("paper_only", "shadow_only", "dashboard_readonly", "live_locked")
OBSERVED_SOAK_KEYS = (
    "observed_soak_days",
    "observed_calendar_days",
    "paper_shadow_soak_days",
    "soak_days",
    "continuous_soak_days",
)
CRITICAL_GAP_KEYS = ("critical_gap_count", "critical_soak_gap_count")
WARNING_GAP_KEYS = ("warning_gap_count", "soak_gap_count")
MAX_GAP_KEYS = ("max_gap_minutes", "max_soak_gap_minutes")
STATUS_KEYS = ("status", "readiness_status", "continuity_status")


def audit_paper_shadow_soak_anchor_continuity_pack(
    *,
    project_root: str | Path = ".",
    output: str | Path = DEFAULT_OUTPUT_PATH,
    diagnostic_soak_days: int = DEFAULT_DIAGNOSTIC_SOAK_DAYS,
    required_soak_days: int = DEFAULT_REQUIRED_SOAK_DAYS,
    write: bool = False,
    now: datetime | None = None,
) -> SoakAnchorAuditResult:
    root = Path(project_root).resolve()
    generated_at = ensure_utc(now or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    output_path = resolve_under_root(root, output)

    evidence_payloads: dict[str, Mapping[str, Any]] = {}
    evidence_sources: list[dict[str, Any]] = []
    invalid_evidence: list[dict[str, str]] = []

    for source in iter_soak_evidence_sources():
        source_path = root / source.path
        source_record: dict[str, Any] = {
            "name": source.name,
            "path": source.path,
            "exists": source_path.exists(),
            "required_for_anchor": source.required_for_anchor,
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
                source_record["status"] = "invalid"
            else:
                evidence_payloads[source.name] = payload
                source_record["status"] = first_string(payload, STATUS_KEYS)
        elif source_path.exists():
            source_record["status"] = "versioned_file_present"
        evidence_sources.append(source_record)

    missing_required_anchor_evidence = sorted(
        source.name
        for source in iter_soak_evidence_sources()
        if source.required_for_anchor and not (root / source.path).exists()
    )
    missing_required_readiness_evidence = sorted(
        source.name
        for source in iter_soak_evidence_sources()
        if source.required_for_readiness and not (root / source.path).exists()
    )
    anchor_family_present = any(
        record["name"] in ANCHOR_FAMILY_NAMES and record["exists"] for record in evidence_sources
    )

    observed_soak_days = max_float(find_numeric_values(evidence_payloads.values(), OBSERVED_SOAK_KEYS))
    critical_gap_count = max_int(find_numeric_values(evidence_payloads.values(), CRITICAL_GAP_KEYS), default=0)
    warning_gap_count = max_int(find_numeric_values(evidence_payloads.values(), WARNING_GAP_KEYS), default=0)
    max_gap_minutes = max_float(find_numeric_values(evidence_payloads.values(), MAX_GAP_KEYS))

    blocking_reasons: list[str] = []
    warning_reasons: list[str] = []

    blocking_reasons.extend(collect_safety_violations(evidence_payloads))
    if invalid_evidence:
        blocking_reasons.append("invalid_json_evidence_present")
    if not anchor_family_present:
        blocking_reasons.append("missing_soak_anchor_family_evidence")
    if observed_soak_days is None:
        warning_reasons.append("observed_soak_days_unknown")
    elif observed_soak_days < float(required_soak_days):
        blocking_reasons.append(
            f"required_soak_days_not_reached: observed_soak_days={observed_soak_days:.4f}, required_soak_days={required_soak_days}"
        )
    if critical_gap_count > 0:
        blocking_reasons.append(f"critical_gap_count_gt_zero: {critical_gap_count}")
    if missing_required_readiness_evidence:
        warning_reasons.append("missing_required_readiness_evidence")
    if warning_gap_count > 0:
        warning_reasons.append(f"warning_gap_count_gt_zero: {warning_gap_count}")

    diagnostic_soak_reached = observed_soak_days is not None and observed_soak_days >= float(diagnostic_soak_days)
    readiness_soak_reached = observed_soak_days is not None and observed_soak_days >= float(required_soak_days)
    continuity_anchor_established = anchor_family_present and not invalid_evidence
    readiness_anchor_established = (
        continuity_anchor_established
        and readiness_soak_reached
        and critical_gap_count == 0
        and not collect_safety_violations(evidence_payloads)
    )

    if not continuity_anchor_established:
        status = "evidence_missing"
        reason = "missing_or_invalid_soak_anchor_evidence"
    elif blocking_reasons:
        status = "blocked"
        reason = ";".join(sorted(set(blocking_reasons)))
    elif warning_reasons:
        status = "degraded"
        reason = ";".join(sorted(set(warning_reasons)))
    else:
        status = "ok"
        reason = "paper_shadow_soak_anchor_current"

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "project_name": PROJECT_NAME,
        "dashboard_name": DEFAULT_DASHBOARD_NAME,
        "generated_at_utc": generated_at,
        "status": status,
        "reason": reason,
        "diagnostic_soak_days": int(diagnostic_soak_days),
        "required_soak_days": int(required_soak_days),
        "observed_soak_days": observed_soak_days,
        "seven_day_diagnostic_status": gate_status(
            reached=diagnostic_soak_reached,
            blocked=not continuity_anchor_established,
            known=observed_soak_days is not None,
        ),
        "thirty_day_readiness_status": gate_status(
            reached=readiness_soak_reached and critical_gap_count == 0 and not blocking_reasons,
            blocked=bool(blocking_reasons),
            known=observed_soak_days is not None,
        ),
        "diagnostic_soak_reached": diagnostic_soak_reached,
        "readiness_soak_reached": readiness_soak_reached,
        "critical_gap_count": critical_gap_count,
        "warning_gap_count": warning_gap_count,
        "max_gap_minutes": max_gap_minutes,
        "continuity_anchor_established": continuity_anchor_established,
        "readiness_anchor_established": readiness_anchor_established,
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
        "missing_required_anchor_evidence": missing_required_anchor_evidence,
        "missing_required_readiness_evidence": missing_required_readiness_evidence,
        "invalid_evidence": invalid_evidence,
        "blocking_reasons": sorted(set(blocking_reasons)),
        "warning_reasons": sorted(set(warning_reasons)),
        "next_required_actions": next_required_actions(
            status=status,
            observed_soak_days=observed_soak_days,
            required_soak_days=required_soak_days,
            critical_gap_count=critical_gap_count,
            missing_required_readiness_evidence=missing_required_readiness_evidence,
        ),
        "evidence_sources": evidence_sources,
        "safety": {
            "paper_only": True,
            "shadow_only": True,
            "dashboard_readonly": True,
            "live_locked": True,
            "live_trading_enabled": False,
            "live_release_allowed": False,
            "canary_release_allowed": False,
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
            "rebuilds_dataset": False,
        },
        "write_performed": False,
    }

    write_performed = False
    if write:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        report_to_write = dict(report)
        report_to_write["write_performed"] = True
        output_path.write_text(json.dumps(report_to_write, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        write_performed = True
        report["write_performed"] = True

    return SoakAnchorAuditResult(report=report, output_path=output_path, write_performed=write_performed)


def load_json_object(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise TypeError(f"Expected JSON object at {path}")
    return payload


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def resolve_under_root(root: Path, candidate: str | Path) -> Path:
    path = Path(candidate)
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Output path must remain under project root: {candidate}") from exc
    return resolved


def first_string(payload: Mapping[str, Any], keys: Iterable[str]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def find_numeric_values(payloads: Iterable[Any], keys: Iterable[str]) -> list[float]:
    wanted = set(keys)
    values: list[float] = []

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if key in wanted:
                    parsed = parse_float(nested)
                    if parsed is not None:
                        values.append(parsed)
                walk(nested)
        elif isinstance(value, list | tuple):
            for item in value:
                walk(item)

    for payload in payloads:
        walk(payload)
    return values


def max_float(values: Iterable[float]) -> float | None:
    parsed = list(values)
    if not parsed:
        return None
    return max(parsed)


def max_int(values: Iterable[float], *, default: int = 0) -> int:
    parsed = list(values)
    if not parsed:
        return default
    return int(max(parsed))


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


def collect_safety_violations(payloads: Mapping[str, Mapping[str, Any]]) -> list[str]:
    violations: list[str] = []
    for source_name, payload in payloads.items():
        for flag in UNSAFE_TRUE_FLAGS:
            if any_exact_bool(payload, flag, True):
                violations.append(f"unsafe_source_safety_flag:{source_name}:{flag}=true")
        for flag in SAFE_TRUE_FLAGS:
            if any_exact_bool(payload, flag, False):
                violations.append(f"unsafe_source_safety_flag:{source_name}:{flag}=false")
    return sorted(set(violations))


def any_exact_bool(payload: Any, key: str, expected: bool) -> bool:
    if isinstance(payload, Mapping):
        for current_key, value in payload.items():
            if current_key == key and value is expected:
                return True
            if any_exact_bool(value, key, expected):
                return True
    elif isinstance(payload, list | tuple):
        return any(any_exact_bool(item, key, expected) for item in payload)
    return False


def gate_status(*, reached: bool, blocked: bool, known: bool) -> str:
    if blocked:
        return "blocked"
    if not known:
        return "unknown"
    return "reached" if reached else "not_reached"


def next_required_actions(
    *,
    status: str,
    observed_soak_days: float | None,
    required_soak_days: int,
    critical_gap_count: int,
    missing_required_readiness_evidence: list[str],
) -> list[str]:
    actions: list[str] = []
    if observed_soak_days is None:
        actions.append("Gerar evidência paper/shadow com observed_soak_days para ancorar a continuidade.")
    elif observed_soak_days < float(required_soak_days):
        remaining = max(float(required_soak_days) - observed_soak_days, 0.0)
        actions.append(f"Manter soak paper/shadow até completar 30 dias canônicos; faltam {remaining:.2f} dias.")
    if critical_gap_count > 0:
        actions.append("Investigar gaps críticos antes de qualquer readiness/canary review.")
    if missing_required_readiness_evidence:
        actions.append("Materializar evidências obrigatórias ausentes antes de readiness review: " + ", ".join(missing_required_readiness_evidence))
    if status in {"ok", "degraded"}:
        actions.append("Manter canary/live bloqueados até go/no-go manual e gates futuros explícitos.")
    return actions
