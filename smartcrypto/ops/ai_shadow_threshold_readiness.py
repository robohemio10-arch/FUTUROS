from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = "ai_shadow_threshold_readiness_evidence_v1"
DEFAULT_OUTPUT_PATH = Path("data/reports/ai_shadow_threshold_readiness_evidence.json")
DEFAULT_MIN_DECISIONS = 500
DEFAULT_MIN_ACCEPTANCE_RATE = 0.01
DEFAULT_MAX_ACCEPTANCE_RATE = 0.99
DEFAULT_MIN_PROFIT_FACTOR = 1.0

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
    "changes_model",
    "promotes_model",
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
    "changes_model": False,
    "promotes_model": False,
    "live_release_allowed": False,
}

EVIDENCE_PATHS: dict[str, str] = {
    "ai_shadow_decision_db_audit": "data/reports/ai_shadow_filter_decision_db_audit_summary.json",
    "ai_shadow_incremental_summary": "data/reports/ai_shadow_filter_incremental_daily_summary.json",
    "ai_shadow_financial_threshold_report": "data/reports/ai_shadow_financial_threshold_evaluation_report.json",
    "daily_ai_shadow_update_summary": "data/reports/daily_ai_shadow_update_summary.json",
    "ai_shadow_drift_report": "data/reports/ai_shadow_drift_monitor_report.json",
    "runtime_evidence_pack_v2": "data/reports/runtime_evidence_pack_v2.json",
    "readiness_snapshot_v2": "data/reports/readiness_snapshot_v2.json",
    "paper_shadow_soak_continuity": "data/reports/paper_shadow_soak_continuity_audit.json",
    "monte_carlo_no_trade_recovery": "data/reports/monte_carlo_no_trade_recovery_diagnostics.json",
}

CORE_AI_SHADOW_EVIDENCE = (
    "ai_shadow_decision_db_audit",
    "ai_shadow_financial_threshold_report",
    "ai_shadow_incremental_summary",
    "daily_ai_shadow_update_summary",
)

ACCEPT_KEYS = ("AI_ACCEPT", "ai_accept", "accepted", "accept_count", "ai_accept_count")
REJECT_KEYS = ("AI_REJECT", "ai_reject", "rejected", "reject_count", "ai_reject_count")
TOTAL_KEYS = ("rows", "total_rows", "total_input_rows", "dataset_rows", "decisions", "decision_count")
PROFIT_FACTOR_KEYS = ("profit_factor", "pf", "shadow_filtered_profit_factor", "shadow_profit_factor")
NET_PNL_KEYS = ("net_pnl", "shadow_filtered_net_pnl", "shadow_net_pnl", "pnl", "profit")
DRAWDOWN_KEYS = ("max_drawdown", "mdd", "shadow_filtered_max_drawdown", "max_dd")


@dataclass(frozen=True)
class EvidenceResult:
    report: dict[str, Any]
    output_path: Path
    write_performed: bool


def build_ai_shadow_threshold_readiness_evidence(
    *,
    project_root: str | Path = ".",
    output: str | Path = DEFAULT_OUTPUT_PATH,
    min_decisions: int = DEFAULT_MIN_DECISIONS,
    min_acceptance_rate: float = DEFAULT_MIN_ACCEPTANCE_RATE,
    max_acceptance_rate: float = DEFAULT_MAX_ACCEPTANCE_RATE,
    min_profit_factor: float = DEFAULT_MIN_PROFIT_FACTOR,
    no_write: bool = False,
    now: datetime | None = None,
) -> EvidenceResult:
    root = Path(project_root).resolve()
    output_path = resolve_under_root(root, output)
    generated_at = iso(now or datetime.now(timezone.utc))

    payloads: dict[str, Mapping[str, Any]] = {}
    missing_evidence: list[str] = []
    invalid_evidence: list[dict[str, str]] = []
    evidence_sources: list[dict[str, Any]] = []

    for name, relative_path in EVIDENCE_PATHS.items():
        path = root / relative_path
        if not path.exists():
            missing_evidence.append(name)
            continue
        try:
            payload = load_json_object(path)
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
            invalid_evidence.append({"name": name, "path": relative_path, "error": f"{type(exc).__name__}: {exc}"})
            continue
        payloads[name] = payload
        evidence_sources.append({"name": name, "path": relative_path, "status": payload.get("status"), "exists": True})

    metrics = collect_threshold_metrics(payloads)
    safety_violations = collect_safety_violations(payloads)
    categories = classify_threshold_categories(payloads)
    blocking_reasons: list[str] = []
    warning_reasons: list[str] = []
    next_required_actions: list[str] = []

    if safety_violations:
        blocking_reasons.extend(safety_violations)
    if invalid_evidence:
        warning_reasons.append("invalid_evidence_present")
    if missing_evidence:
        warning_reasons.append("missing_evidence_present")

    core_present = any(name in payloads for name in CORE_AI_SHADOW_EVIDENCE)
    if not core_present:
        next_required_actions.append("Gerar auditoria AI Shadow ou relatório financeiro de threshold antes de usar evidência de readiness.")

    total_decisions = metrics["total_decisions"]
    accepted = metrics["accepted_decisions"]
    rejected = metrics["rejected_decisions"]
    acceptance_rate = metrics["acceptance_rate"]
    profit_factor = metrics["profit_factor"]

    if total_decisions is None or total_decisions < min_decisions:
        blocking_reasons.append(f"min_decisions_not_reached: observed={total_decisions}, required={min_decisions}")
        next_required_actions.append("Acumular mais decisões AI Shadow quality-gated antes de readiness.")
    if accepted is None or rejected is None:
        blocking_reasons.append("accept_reject_counts_missing")
        next_required_actions.append("Gerar auditoria SQLite AI Shadow com contagens AI_ACCEPT e AI_REJECT.")
    if acceptance_rate is not None and acceptance_rate < min_acceptance_rate:
        blocking_reasons.append(f"acceptance_rate_below_minimum: {acceptance_rate:.6f} < {min_acceptance_rate}")
    if acceptance_rate is not None and acceptance_rate > max_acceptance_rate:
        blocking_reasons.append(f"acceptance_rate_above_maximum: {acceptance_rate:.6f} > {max_acceptance_rate}")
    if profit_factor is not None and profit_factor < min_profit_factor:
        blocking_reasons.append(f"profit_factor_below_minimum: {profit_factor:.6f} < {min_profit_factor}")
        next_required_actions.append("Investigar threshold AI Shadow; não promover modelo nem relaxar risco automaticamente.")
    if "drift_or_schema_block" in categories:
        blocking_reasons.append("ai_shadow_drift_or_schema_block_detected")
    if "threshold_quality_warning" in categories:
        warning_reasons.append("threshold_quality_warning_detected")

    if not core_present:
        status = "evidence_missing"
    elif blocking_reasons:
        status = "blocked"
    elif warning_reasons or categories:
        status = "degraded"
    else:
        status = "ok"

    threshold_readiness_evidence_approved = status == "ok"

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "project_root": str(root),
        "status": status,
        "threshold_readiness_evidence_approved": threshold_readiness_evidence_approved,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "changes_model": False,
        "promotes_model": False,
        "changes_risk": False,
        "sends_orders": False,
        "min_decisions": min_decisions,
        "min_acceptance_rate": min_acceptance_rate,
        "max_acceptance_rate": max_acceptance_rate,
        "min_profit_factor": min_profit_factor,
        "metrics": metrics,
        "root_cause_categories": sorted(categories),
        "blocking_reasons": sorted(set(blocking_reasons)),
        "warning_reasons": sorted(set(warning_reasons)),
        "next_required_actions": sorted(set(next_required_actions)),
        "missing_evidence": sorted(missing_evidence),
        "invalid_evidence": invalid_evidence,
        "evidence_sources": evidence_sources,
        "safety_flags": dict(SAFETY_FLAGS),
    }

    write_performed = False
    if not no_write:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_performed = True

    return EvidenceResult(report=report, output_path=output_path, write_performed=write_performed)


def collect_threshold_metrics(payloads: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    accepted = max_number_for_keys(payloads, ACCEPT_KEYS)
    rejected = max_number_for_keys(payloads, REJECT_KEYS)
    total = max_number_for_keys(payloads, TOTAL_KEYS)
    if accepted is not None and rejected is not None:
        total = max(total or 0.0, accepted + rejected)
    acceptance_rate = None
    if accepted is not None and total and total > 0:
        acceptance_rate = accepted / total
    return {
        "accepted_decisions": as_int_or_none(accepted),
        "rejected_decisions": as_int_or_none(rejected),
        "total_decisions": as_int_or_none(total),
        "acceptance_rate": None if acceptance_rate is None else round(acceptance_rate, 6),
        "profit_factor": max_number_for_keys(payloads, PROFIT_FACTOR_KEYS),
        "net_pnl": max_number_for_keys(payloads, NET_PNL_KEYS),
        "max_drawdown": min_number_for_keys(payloads, DRAWDOWN_KEYS),
    }


def classify_threshold_categories(payloads: Mapping[str, Mapping[str, Any]]) -> set[str]:
    categories: set[str] = set()
    for name, payload in payloads.items():
        status = normalize(payload.get("status"))
        reason = normalize(payload.get("reason"))
        blocking = normalize_list(payload.get("blocking_reasons")) + normalize_list(payload.get("validation_errors"))
        text = " ".join([name, status, reason, *blocking])
        if contains_any(text, ("threshold", "acceptance", "reject", "quality")):
            categories.add("threshold_quality_warning")
        if contains_any(text, ("drift", "schema", "missing", "orphan", "extra")):
            categories.add("drift_or_schema_block")
        if contains_any(text, ("pnl", "profit_factor", "drawdown", "financial")):
            categories.add("financial_evidence_present")
        if contains_any(text, ("readiness", "soak", "monte", "no_trade")):
            categories.add("upstream_readiness_context_present")
    return categories


def max_number_for_keys(payloads: Mapping[str, Mapping[str, Any]], keys: Iterable[str]) -> float | None:
    values: list[float] = []
    key_set = set(keys)
    for payload in payloads.values():
        for key, value in iter_key_values(payload):
            if key in key_set:
                parsed = parse_number(value)
                if parsed is not None:
                    values.append(parsed)
    return max(values) if values else None


def min_number_for_keys(payloads: Mapping[str, Mapping[str, Any]], keys: Iterable[str]) -> float | None:
    values: list[float] = []
    key_set = set(keys)
    for payload in payloads.values():
        for key, value in iter_key_values(payload):
            if key in key_set:
                parsed = parse_number(value)
                if parsed is not None:
                    values.append(parsed)
    return min(values) if values else None


def parse_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def as_int_or_none(value: float | None) -> int | None:
    return None if value is None else int(value)


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
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def normalize_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [normalize(item) for item in value]
    if value is None:
        return []
    return [normalize(value)]


def contains_any(text: str, needles: Iterable[str]) -> bool:
    normalized = normalize(text)
    return any(needle in normalized for needle in needles)


def is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
    return bool(value)


def collect_safety_violations(payloads: Mapping[str, Mapping[str, Any]]) -> list[str]:
    violations: list[str] = []
    for name, payload in payloads.items():
        for key, value in iter_key_values(payload):
            if key in SAFE_TRUE_FLAGS and value is False:
                violations.append(f"{name}:{key}=false")
            if key in SAFE_FALSE_FLAGS and is_truthy(value):
                violations.append(f"{name}:{key}=true")
    return sorted(set(violations))


def iter_key_values(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            yield str(key), nested
            if isinstance(nested, (Mapping, list, tuple)):
                yield from iter_key_values(nested)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from iter_key_values(item)
