from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = "monte_carlo_no_trade_recovery_diagnostics_v1"
DEFAULT_OUTPUT_PATH = Path("data/reports/monte_carlo_no_trade_recovery_diagnostics.json")

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
    "monte_carlo_report": "data/reports/monte_carlo_risk_simulation_report.json",
    "risk_budget_report": "data/reports/monte_carlo_risk_budget_policy_report.json",
    "runtime_evidence_pack_v2": "data/reports/runtime_evidence_pack_v2.json",
    "readiness_snapshot_v2": "data/reports/readiness_snapshot_v2.json",
    "paper_shadow_soak_continuity": "data/reports/paper_shadow_soak_continuity_audit.json",
    "paper_soak_report": "data/reports/paper_soak_report.json",
    "paper_shadow_soak_report": "data/reports/paper_shadow_soak_report.json",
    "freqtrade_paper_db_authority_report": "data/reports/freqtrade_paper_db_authority_report.json",
    "market_data_health_report": "data/reports/market_data_health_audit_report.json",
    "market_data_runtime_sources_report": "data/reports/market_data_health_runtime_sources_report.json",
    "qlib_fresh_predictions_report": "data/reports/qlib_fresh_predictions_summary.json",
    "active_signals_report": "data/reports/phase13_active_signals_summary.json",
    "ai_shadow_incremental_summary": "data/reports/ai_shadow_filter_incremental_daily_summary.json",
    "ai_shadow_decision_db_audit": "data/reports/ai_shadow_filter_decision_db_audit_summary.json",
}

MINIMUM_EVIDENCE = ("monte_carlo_report", "risk_budget_report", "runtime_evidence_pack_v2", "readiness_snapshot_v2")
NO_TRADE_STATUSES = {"no_trade", "no-trade", "blocked", "risk_blocked", "halted", "insufficient_evidence"}


@dataclass(frozen=True)
class DiagnosticResult:
    report: dict[str, Any]
    output_path: Path
    write_performed: bool


def build_monte_carlo_no_trade_recovery_diagnostics(
    *,
    project_root: str | Path = ".",
    output: str | Path = DEFAULT_OUTPUT_PATH,
    no_write: bool = False,
    now: datetime | None = None,
) -> DiagnosticResult:
    root = Path(project_root).resolve()
    output_path = resolve_under_root(root, output)
    generated_at = iso(now or datetime.now(timezone.utc))

    missing_evidence: list[str] = []
    invalid_evidence: list[dict[str, str]] = []
    evidence_sources: list[dict[str, Any]] = []
    payloads: dict[str, Mapping[str, Any]] = {}

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

    safety_violations = collect_safety_violations(payloads)
    monte_carlo_payload = payloads.get("monte_carlo_report", {})
    risk_budget_payload = payloads.get("risk_budget_report", {})
    readiness_payload = payloads.get("readiness_snapshot_v2", {})

    no_trade_detected, no_trade_markers = detect_no_trade(payloads)
    categories = classify_root_causes(payloads=payloads, missing_evidence=missing_evidence, invalid_evidence=invalid_evidence)
    recovery_actions = build_recovery_actions(categories=categories, no_trade_detected=no_trade_detected)

    blocking_reasons: list[str] = []
    warning_reasons: list[str] = []

    if safety_violations:
        blocking_reasons.extend(safety_violations)
    if invalid_evidence:
        warning_reasons.append("invalid_evidence_present")
    if missing_evidence:
        warning_reasons.append("missing_evidence_present")
    if no_trade_detected:
        blocking_reasons.append("monte_carlo_or_readiness_no_trade_detected")
    if is_truthy(readiness_payload.get("live_release_allowed")):
        blocking_reasons.append("readiness_snapshot_live_release_allowed_true")
    if is_truthy(monte_carlo_payload.get("live_release_allowed")):
        blocking_reasons.append("monte_carlo_report_live_release_allowed_true")
    if is_truthy(risk_budget_payload.get("live_release_allowed")):
        blocking_reasons.append("risk_budget_report_live_release_allowed_true")

    minimum_present = any(name in payloads for name in MINIMUM_EVIDENCE)
    if not minimum_present:
        status = "evidence_missing"
    elif blocking_reasons:
        status = "blocked"
    elif warning_reasons or categories:
        status = "degraded"
    else:
        status = "ok"

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "project_root": str(root),
        "status": status,
        "no_trade_detected": no_trade_detected,
        "no_trade_markers": no_trade_markers,
        "root_cause_categories": sorted(categories),
        "blocking_reasons": sorted(set(blocking_reasons)),
        "warning_reasons": sorted(set(warning_reasons)),
        "missing_evidence": sorted(missing_evidence),
        "invalid_evidence": invalid_evidence,
        "recovery_actions": recovery_actions,
        "evidence_sources": evidence_sources,
        "diagnostic_summary": build_diagnostic_summary(payloads),
        "safety_flags": dict(SAFETY_FLAGS),
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "changes_risk": False,
        "sends_orders": False,
    }

    write_performed = False
    if not no_write:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_performed = True

    return DiagnosticResult(report=report, output_path=output_path, write_performed=write_performed)


def classify_root_causes(
    *,
    payloads: Mapping[str, Mapping[str, Any]],
    missing_evidence: list[str],
    invalid_evidence: list[dict[str, str]],
) -> set[str]:
    categories: set[str] = set()
    if invalid_evidence:
        categories.add("invalid_evidence")
    if any(name in missing_evidence for name in MINIMUM_EVIDENCE):
        categories.add("missing_core_evidence")

    for name, payload in payloads.items():
        status = normalize(payload.get("status"))
        reason = normalize(payload.get("reason"))
        blocking = normalize_list(payload.get("blocking_reasons")) + normalize_list(payload.get("validation_errors"))
        text_blob = " ".join([name, status, reason, *blocking])
        if contains_any(text_blob, ("stale", "freshness", "market_data", "input_data_stale")):
            categories.add("market_data_stale_or_missing")
        if contains_any(text_blob, ("drawdown", "ruin", "cvar", "max_loss", "risk_budget")):
            categories.add("risk_budget_or_drawdown_block")
        if contains_any(text_blob, ("sample", "insufficient", "min_trades", "evidence_missing")):
            categories.add("insufficient_sample_or_evidence")
        if contains_any(text_blob, ("soak", "continuity", "gap", "required_soak")):
            categories.add("soak_or_continuity_block")
        if contains_any(text_blob, ("ai_shadow", "threshold", "reject", "quality")):
            categories.add("ai_shadow_quality_gate_block")
        if contains_any(text_blob, ("prediction", "qlib", "signal", "no_signal")):
            categories.add("prediction_or_signal_absence")
        if contains_any(text_blob, ("manifest", "secret", "safety", "live", "order_submission", "private")):
            categories.add("safety_or_audit_block")
    return categories


def build_recovery_actions(*, categories: set[str], no_trade_detected: bool) -> list[str]:
    actions: list[str] = []
    if no_trade_detected:
        actions.append("Manter live/canário bloqueado até Monte Carlo sair de no_trade com evidência suficiente.")
    if "missing_core_evidence" in categories:
        actions.append("Gerar ou recuperar relatórios centrais de Monte Carlo, risk budget, runtime evidence e readiness snapshot.")
    if "invalid_evidence" in categories:
        actions.append("Corrigir JSONs inválidos antes de usar qualquer diagnóstico para readiness.")
    if "market_data_stale_or_missing" in categories:
        actions.append("Atualizar market features/predictions e validar freshness antes de rerodar Monte Carlo.")
    if "risk_budget_or_drawdown_block" in categories:
        actions.append("Investigar drawdown, risk-of-ruin, CVaR e stress de custos; não relaxar risco automaticamente.")
    if "insufficient_sample_or_evidence" in categories:
        actions.append("Acumular mais histórico paper/shadow ou aumentar amostra validada antes de nova avaliação.")
    if "soak_or_continuity_block" in categories:
        actions.append("Resolver gaps de continuidade do paper/shadow soak antes de considerar readiness.")
    if "ai_shadow_quality_gate_block" in categories:
        actions.append("Auditar thresholds e distribuição AI_ACCEPT/AI_REJECT sem promover modelo automaticamente.")
    if "prediction_or_signal_absence" in categories:
        actions.append("Validar Qlib predictions e geração de sinais ativos antes de rerodar Monte Carlo.")
    if "safety_or_audit_block" in categories:
        actions.append("Resolver violações de safety/manifest/secret scan antes de qualquer avanço operacional.")
    actions.append("Reexecutar diagnóstico após corrigir evidências; live_release_allowed deve permanecer false.")
    return sorted(set(actions))


def detect_no_trade(payloads: Mapping[str, Mapping[str, Any]]) -> tuple[bool, list[dict[str, str]]]:
    markers: list[dict[str, str]] = []
    for name, payload in payloads.items():
        for key, value in iter_key_values(payload):
            normalized = normalize(value)
            if normalized in NO_TRADE_STATUSES or "no_trade" in normalized or "no trade" in normalized:
                markers.append({"evidence": name, "key": key, "value": str(value)})
    return bool(markers), markers


def build_diagnostic_summary(payloads: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for name, payload in payloads.items():
        summary[name] = {
            "status": payload.get("status"),
            "reason": payload.get("reason"),
            "live_release_allowed": payload.get("live_release_allowed"),
            "blocking_reasons_count": count_list(payload.get("blocking_reasons")),
            "validation_errors_count": count_list(payload.get("validation_errors")),
        }
    return summary


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
    if isinstance(value, list | tuple):
        return [normalize(item) for item in value]
    if value is None:
        return []
    return [normalize(value)]


def contains_any(text: str, needles: Iterable[str]) -> bool:
    normalized = normalize(text)
    return any(needle in normalized for needle in needles)


def count_list(value: Any) -> int:
    if isinstance(value, list | tuple):
        return len(value)
    if value in (None, ""):
        return 0
    return 1


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
            if isinstance(nested, Mapping | list | tuple):
                yield from iter_key_values(nested)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from iter_key_values(item)
