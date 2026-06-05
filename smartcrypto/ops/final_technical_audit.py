from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from smartcrypto.ops.paper_shadow_soak_report import monte_carlo_policy_summary

DEFAULT_REPORTS_ROOT = Path("data/reports")
DEFAULT_OUTPUT_PATH = Path("data/reports/final_technical_audit_20_pillars_report.json")
ROADMAP_VERSION = "canonical_20_pillars_v1"
AUDIT_VERSION = "final_technical_audit_v1"
SAFE_FALSE_FLAGS = (
    "live_trading_enabled",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "changes_risk",
)

REPORT_FILES = {
    "readiness_gate": "readiness_gate_report.json",
    "paper_soak": "paper_soak_report.json",
    "system_healthcheck": "system_healthcheck_report.json",
    "backup_snapshot": "backup_snapshot_report.json",
    "restore_dry_run": "restore_dry_run_report.json",
    "sklearn_compatibility": "sklearn_model_compatibility_guard_report.json",
    "runtime_safety": "runtime_safety_config_validation_report.json",
    "critical_alerting": "critical_alerting_report.json",
    "financial_event_log": "financial_event_log.jsonl",
    "risk_recovery": "risk_recovery_mode_audit_report.json",
    "market_data_health": "market_data_health_audit_report.json",
    "state_reconciliation": "state_reconciliation_audit_report.json",
    "ledger": "order_intent_capital_ledger_audit_report.json",
    "ai_governance": "ai_governance_dashboard_sources_report.json",
    "risk_readiness_soak_dashboard": "risk_readiness_soak_dashboard_sources_report.json",
    "drift_monitor": "ai_shadow_drift_monitor_report.json",
    "financial_thresholds": "ai_shadow_financial_threshold_evaluation_report.json",
    "anti_leakage": "phase23_anti_leakage_report.json",
    "monte_carlo": "monte_carlo_risk_simulation_report.json",
    "monte_carlo_risk_budget_policy": "monte_carlo_risk_budget_policy_report.json",
    "event_backtest": "event_driven_backtest_report.json",
    "data_quality": "data_quality_report.json",
    "dataset_manifest": "dataset_manifest.json",
    "model_registry_gate": "model_registry_promotion_gate_report.json",
    "ai_shadow_trainer": "ai_shadow_incremental_trainer_report.json",
}
OPTIONAL_REPORT_KEYS = {"monte_carlo_risk_budget_policy"}

PILLARS = [
    {"id": 1, "name": "Arquitetura", "previous_score": 7.0, "evidence": ["runtime_safety", "system_healthcheck", "data_quality"], "gates": ["runtime_safety", "system_healthcheck"]},
    {"id": 2, "name": "Operabilidade", "previous_score": 7.0, "evidence": ["readiness_gate", "paper_soak", "critical_alerting", "financial_event_log"], "gates": ["readiness_gate", "critical_alerting", "paper_soak"]},
    {"id": 3, "name": "Escalabilidade multimercado", "previous_score": 6.0, "evidence": ["dataset_manifest", "market_data_health", "data_quality"], "gates": ["market_data_health", "data_quality"]},
    {"id": 4, "name": "Testes e manutenibilidade", "previous_score": 7.0, "evidence": ["system_healthcheck", "data_quality", "dataset_manifest"], "gates": ["system_healthcheck", "data_quality"]},
    {"id": 5, "name": "Segurança de chaves", "previous_score": 7.0, "evidence": ["runtime_safety", "system_healthcheck", "backup_snapshot"], "gates": ["runtime_safety", "system_healthcheck"]},
    {"id": 6, "name": "Confiabilidade live", "previous_score": 5.0, "evidence": ["readiness_gate", "paper_soak", "state_reconciliation", "risk_recovery"], "gates": ["readiness_gate", "paper_soak", "state_reconciliation", "risk_recovery"]},
    {"id": 7, "name": "Integridade e latência de dados", "previous_score": 7.0, "evidence": ["market_data_health", "data_quality", "dataset_manifest"], "gates": ["market_data_health", "data_quality"]},
    {"id": 8, "name": "Drift", "previous_score": 7.0, "evidence": ["drift_monitor", "ai_governance"], "gates": ["drift_monitor"]},
    {"id": 9, "name": "Overfitting", "previous_score": 7.0, "evidence": ["anti_leakage", "data_quality", "event_backtest"], "gates": ["anti_leakage", "data_quality"]},
    {"id": 10, "name": "Execução e slippage", "previous_score": 6.0, "evidence": ["ledger", "event_backtest", "state_reconciliation"], "gates": ["ledger", "event_backtest", "state_reconciliation"]},
    {"id": 11, "name": "Maker/taker", "previous_score": 5.0, "evidence": ["ledger", "event_backtest", "financial_thresholds"], "gates": ["ledger", "event_backtest"]},
    {"id": 12, "name": "Métricas ajustadas a risco", "previous_score": 7.0, "evidence": ["monte_carlo", "financial_thresholds", "risk_recovery"], "gates": ["monte_carlo", "risk_recovery"]},
    {"id": 13, "name": "Recuperação de drawdown", "previous_score": 7.0, "evidence": ["risk_recovery", "critical_alerting"], "gates": ["risk_recovery", "critical_alerting"]},
    {"id": 14, "name": "Backtest e Monte Carlo", "previous_score": 7.0, "evidence": ["event_backtest", "monte_carlo", "anti_leakage"], "gates": ["event_backtest", "monte_carlo", "anti_leakage"]},
    {"id": 15, "name": "Dashboard Streamlit", "previous_score": 7.0, "evidence": ["ai_governance", "risk_readiness_soak_dashboard", "readiness_gate"], "gates": ["ai_governance", "risk_readiness_soak_dashboard"]},
    {"id": 16, "name": "Lucratividade líquida", "previous_score": 5.0, "evidence": ["financial_thresholds", "paper_soak", "ai_shadow_trainer"], "gates": ["financial_thresholds", "paper_soak"]},
    {"id": 17, "name": "SaaS", "previous_score": 4.0, "evidence": ["system_healthcheck", "dashboard", "backup_snapshot"], "gates": ["system_healthcheck"]},
    {"id": 18, "name": "Infraestrutura", "previous_score": 7.0, "evidence": ["system_healthcheck", "backup_snapshot", "restore_dry_run"], "gates": ["system_healthcheck", "backup_snapshot", "restore_dry_run"]},
    {"id": 19, "name": "Conformidade legal/fiscal", "previous_score": 4.0, "evidence": ["runtime_safety", "financial_event_log", "critical_alerting"], "gates": ["runtime_safety", "critical_alerting"]},
    {"id": 20, "name": "IA + Docker + Freqtrade + Qlib", "previous_score": 7.0, "evidence": ["sklearn_compatibility", "model_registry_gate", "ai_shadow_trainer", "ai_governance", "system_healthcheck"], "gates": ["sklearn_compatibility", "model_registry_gate", "ai_shadow_trainer", "ai_governance"]},
]


def build_final_technical_audit_report(
    *,
    reports_root: str | Path = DEFAULT_REPORTS_ROOT,
    output_path: str | Path | None = DEFAULT_OUTPUT_PATH,
    project_root: str | Path | None = None,
    monte_carlo_risk_budget_policy_report: str | Path | None = None,
    required_target_score: float = 9.0,
    strict: bool = False,
    now: datetime | None = None,
    safety_overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    generated_at = ensure_utc(now or datetime.now(timezone.utc))
    root = Path(reports_root)
    project = Path(project_root) if project_root is not None else Path.cwd()
    sources = {name: load_source(root / filename, jsonl=filename.endswith(".jsonl")) for name, filename in REPORT_FILES.items()}
    if monte_carlo_risk_budget_policy_report is not None:
        sources["monte_carlo_risk_budget_policy"] = load_source(Path(monte_carlo_risk_budget_policy_report))
    payloads = {name: source["payload"] for name, source in sources.items()}
    gate_summary = build_quality_gates_summary(payloads, sources)
    p0, p1, p2 = collect_findings(payloads, sources)
    global_blockers = build_global_blockers(payloads, sources, gate_summary, p0, p1, strict, safety_overrides)
    monte_carlo_policy = monte_carlo_policy_summary(
        payloads.get("monte_carlo_risk_budget_policy", {}),
        source_exists=sources.get("monte_carlo_risk_budget_policy", {}).get("exists", False),
    )
    missing_evidence = sorted(name for name, source in sources.items() if not source["exists"] and name not in OPTIONAL_REPORT_KEYS)
    pillars = [
        classify_pillar(
            definition,
            sources=sources,
            gates=gate_summary,
            p0_findings=p0,
            p1_findings=p1,
            required_target_score=required_target_score,
        )
        for definition in PILLARS
    ]
    safety = safety_payload(safety_overrides)
    readiness_approved = bool(payloads["readiness_gate"].get("readiness_approved")) and gate_summary["readiness_gate"]["status"] == "ok"
    status = resolve_global_status(global_blockers, missing_evidence, gate_summary, p1)
    report = {
        "status": status,
        "reason": ";".join(global_blockers or (["missing_evidence"] if status == "warning" else ["final_technical_audit_ok"])),
        "generated_at_utc": iso(generated_at),
        "project_root": str(project),
        "roadmap_version": ROADMAP_VERSION,
        "audit_version": AUDIT_VERSION,
        "overall_score": round(sum(float(pillar["current_score"]) for pillar in pillars) / len(pillars), 2),
        "target_score": float(required_target_score),
        "readiness_approved": readiness_approved,
        "live_release_allowed": False,
        "monte_carlo_risk_budget_policy_status": monte_carlo_policy["status"],
        "monte_carlo_risk_budget_policy_action": monte_carlo_policy["policy_action"],
        "monte_carlo_risk_treated": monte_carlo_policy["monte_carlo_risk_treated"]
        and str(payloads["monte_carlo"].get("status", "")).lower() in {"blocked", "critical", "failed"},
        "no_trade_policy_present": monte_carlo_policy["no_trade_policy_present"],
        "readiness_may_proceed": False if monte_carlo_policy["no_trade_policy_present"] else readiness_approved,
        "manual_go_no_go_required": True,
        "paper_shadow_only": True,
        "pillars": pillars,
        "global_blockers": sorted(set(global_blockers)),
        "p0_findings": p0,
        "p1_findings": p1,
        "p2_findings": p2,
        "missing_evidence": missing_evidence,
        "evidence_summary": evidence_summary(sources),
        "quality_gates_summary": gate_summary,
        "next_required_actions": next_required_actions(global_blockers, missing_evidence, pillars),
        "final_recommendation": final_recommendation(status),
        **safety,
    }
    write_report(report, output_path)
    return report


def classify_pillar(
    definition: Mapping[str, Any],
    *,
    sources: Mapping[str, Mapping[str, Any]],
    gates: Mapping[str, Mapping[str, Any]],
    p0_findings: list[str],
    p1_findings: list[str],
    required_target_score: float,
) -> dict[str, Any]:
    evidence_keys = list(definition["evidence"])
    gate_keys = list(definition["gates"])
    missing = sorted(key for key in evidence_keys if key not in sources or not sources[key]["exists"])
    failed = sorted(key for key in gate_keys if gates.get(key, {}).get("status") == "blocked")
    warning = sorted(key for key in gate_keys if gates.get(key, {}).get("status") in {"warning", "missing"})
    p0 = list(p0_findings)
    p1 = list(p1_findings)
    if failed or p0:
        status = "blocked"
        score = min(6.0, float(definition["previous_score"]))
    elif missing:
        status = "insufficient_evidence"
        score = min(7.0, float(definition["previous_score"]) + 0.5)
    elif warning or p1:
        status = "warning"
        score = min(8.0, float(definition["previous_score"]) + 1.0)
    else:
        status = "ok"
        score = float(required_target_score)
    can_support_9 = status == "ok" and score >= required_target_score and not p0 and not p1
    return {
        "id": int(definition["id"]),
        "name": str(definition["name"]),
        "previous_score": float(definition["previous_score"]),
        "current_score": round(score, 2),
        "target_score": float(required_target_score),
        "status": status,
        "evidence_files": [sources[key]["path"] for key in evidence_keys if key in sources and sources[key]["exists"]],
        "passed_gates": sorted(key for key in gate_keys if gates.get(key, {}).get("status") == "ok"),
        "failed_gates": failed,
        "missing_evidence": missing,
        "p0_findings": p0,
        "p1_findings": p1,
        "p2_findings": [],
        "next_required_actions": pillar_actions(status, missing, failed, warning),
        "can_support_9_of_10": bool(can_support_9),
    }


def build_quality_gates_summary(payloads: Mapping[str, dict[str, Any]], sources: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    gates: dict[str, dict[str, Any]] = {}
    for name, source in sources.items():
        payload = payloads[name]
        if not source["exists"]:
            gates[name] = {"status": "missing", "reason": "missing_evidence", "path": source["path"]}
            continue
        status, reason = source_gate_status(name, payload)
        gates[name] = {"status": status, "reason": reason, "path": source["path"]}
    monte_carlo_policy = monte_carlo_policy_summary(
        payloads.get("monte_carlo_risk_budget_policy", {}),
        source_exists=sources.get("monte_carlo_risk_budget_policy", {}).get("exists", False),
    )
    if monte_carlo_policy["unsafe_findings"]:
        gates["monte_carlo_risk_budget_policy"] = {
            "status": "blocked",
            "reason": "unsafe_policy_report",
            "path": sources.get("monte_carlo_risk_budget_policy", {}).get("path"),
        }
    if gates.get("monte_carlo", {}).get("status") == "blocked" and monte_carlo_policy["no_trade_policy_present"]:
        gates["monte_carlo"] = {
            **gates["monte_carlo"],
            "status": "warning",
            "reason": "monte_carlo_no_trade_policy_active",
        }
    return gates


def source_gate_status(name: str, payload: Mapping[str, Any]) -> tuple[str, str]:
    raw_status = str(payload.get("status", "ok")).lower()
    if any_unsafe_flag(payload):
        return "blocked", "unsafe_safety_flags"
    if name == "monte_carlo_risk_budget_policy":
        return "ok", "policy_present"
    if raw_status in {"blocked", "critical", "failed"}:
        return "blocked", f"status_{raw_status}"
    if name == "readiness_gate" and payload.get("readiness_approved") is False:
        return "blocked", "readiness_not_approved"
    if name == "critical_alerting" and int_value(payload.get("critical_alerts")) > 0:
        return "blocked", "critical_alerts_gt_0"
    if name == "risk_recovery" and str(payload.get("recommended_mode", "")).upper() in {"PANIC", "RECONCILING"}:
        return "blocked", f"risk_recovery_mode_{payload.get('recommended_mode')}"
    if name == "state_reconciliation" and as_bool(payload.get("reconciliation_required")):
        return "blocked", "reconciliation_required"
    if name == "paper_soak" and raw_status in {"insufficient_soak", "missing_data"}:
        return "warning", raw_status
    if raw_status in {"warning", "missing_data", "insufficient_evidence"}:
        return "warning", f"status_{raw_status}"
    return "ok", "ok"


def build_global_blockers(
    payloads: Mapping[str, dict[str, Any]],
    sources: Mapping[str, Mapping[str, Any]],
    gates: Mapping[str, Mapping[str, Any]],
    p0_findings: list[str],
    p1_findings: list[str],
    strict: bool,
    safety_overrides: Mapping[str, Any] | None,
) -> list[str]:
    blockers = []
    if p0_findings:
        blockers.append("p0_findings_present")
    if any("live_blocking" in item.lower() for item in p1_findings):
        blockers.append("p1_live_blocking_findings_present")
    safety = safety_payload(safety_overrides)
    for flag in unsafe_flags(safety):
        blockers.append(f"unsafe_safety_flag:{flag}")
    for name, payload in payloads.items():
        for flag in unsafe_flags(payload):
            blockers.append(f"unsafe_source_safety_flag:{name}:{flag}")
    critical_blockers = {
        "readiness_gate": "readiness_gate_blocked",
        "runtime_safety": "runtime_safety_blocked",
        "critical_alerting": "critical_alerting_blocked",
        "market_data_health": "market_data_health_blocked",
        "risk_recovery": "risk_recovery_blocked",
        "state_reconciliation": "state_reconciliation_blocked",
        "ledger": "ledger_audit_blocked",
        "anti_leakage": "anti_leakage_blocked",
        "monte_carlo": "monte_carlo_blocked",
        "monte_carlo_risk_budget_policy": "unsafe_policy_report",
        "event_backtest": "event_backtest_blocked",
        "data_quality": "data_quality_blocked",
        "sklearn_compatibility": "sklearn_compatibility_blocked",
    }
    for key, reason in critical_blockers.items():
        if gates.get(key, {}).get("status") == "blocked":
            blockers.append(reason)
    monte_carlo_policy = monte_carlo_policy_summary(
        payloads.get("monte_carlo_risk_budget_policy", {}),
        source_exists=sources.get("monte_carlo_risk_budget_policy", {}).get("exists", False),
    )
    if gates.get("monte_carlo", {}).get("reason") == "monte_carlo_no_trade_policy_active":
        blockers.append("monte_carlo_no_trade_policy_active")
    if monte_carlo_policy["unsafe_findings"]:
        blockers.append("unsafe_policy_report")
    if gates.get("paper_soak", {}).get("reason") == "insufficient_soak":
        blockers.append("paper_soak_insufficient")
    if strict:
        blockers.extend(f"missing_required_evidence:{name}" for name, source in sources.items() if not source["exists"])
    return sorted(set(blockers))


def collect_findings(payloads: Mapping[str, dict[str, Any]], sources: Mapping[str, Mapping[str, Any]]) -> tuple[list[str], list[str], list[str]]:
    p0: list[str] = []
    p1: list[str] = []
    p2: list[str] = []
    for name, payload in payloads.items():
        p0.extend(normalize_findings(name, payload.get("p0_findings")))
        p1.extend(normalize_findings(name, payload.get("p1_findings")))
        p2.extend(normalize_findings(name, payload.get("p2_findings")))
        for key, target in (("p0_incidents", p0), ("p1_incidents", p1), ("p2_incidents", p2)):
            count = int_value(payload.get(key))
            target.extend(f"{name}:{key}:{index + 1}" for index in range(count))
    for row in sources.get("financial_event_log", {}).get("rows", []):
        severity = str(row.get("severity", "")).upper()
        item = f"financial_event_log:{row.get('event_type', 'event')}:{row.get('message', severity)}"
        if severity == "P0":
            p0.append(item)
        elif severity == "P1":
            p1.append(item)
        elif severity == "P2":
            p2.append(item)
    return sorted(set(p0)), sorted(set(p1)), sorted(set(p2))


def normalize_findings(source: str, value: Any) -> list[str]:
    if isinstance(value, list):
        return [f"{source}:{item}" for item in value]
    if isinstance(value, str) and value:
        return [f"{source}:{value}"]
    return []


def load_source(path: Path, *, jsonl: bool = False) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False, "payload": {}, "rows": [], "status": "missing"}
    try:
        if jsonl:
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            return {"path": str(path), "exists": True, "payload": {"rows": rows}, "rows": rows, "status": "ok"}
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
        if not isinstance(payload, dict):
            payload = {"rows": payload}
        return {"path": str(path), "exists": True, "payload": payload, "rows": list_value(payload.get("rows")), "status": str(payload.get("status", "ok"))}
    except Exception as exc:
        return {"path": str(path), "exists": True, "payload": {"status": "blocked", "error": str(exc)}, "rows": [], "status": "blocked"}


def evidence_summary(sources: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    present = sorted(name for name, source in sources.items() if source["exists"])
    missing = sorted(name for name, source in sources.items() if not source["exists"] and name not in OPTIONAL_REPORT_KEYS)
    return {
        "total_sources": len(sources),
        "present_count": len(present),
        "missing_count": len(missing),
        "present_sources": present,
        "missing_sources": missing,
    }


def next_required_actions(blockers: list[str], missing: list[str], pillars: list[dict[str, Any]]) -> list[str]:
    actions = []
    if missing:
        actions.append("generate_missing_evidence")
    if blockers:
        actions.append("keep_live_disabled")
        actions.append("resolve_global_blockers")
    if "monte_carlo_no_trade_policy_active" in blockers:
        actions.append("respect_no_trade_policy")
        actions.append("improve_expectancy_before_readiness")
        actions.append("collect_more_paper_shadow_evidence")
    if any("readiness_gate" in blocker for blocker in blockers):
        actions.append("rerun_readiness_gate")
    if any("sklearn" in blocker for blocker in blockers):
        actions.append("align_sklearn_model_runtime_versions")
    if any(not pillar["can_support_9_of_10"] for pillar in pillars):
        actions.append("complete_pillar_evidence_before_9_of_10")
    return sorted(set(actions or ["manual_go_no_go_review"]))


def pillar_actions(status: str, missing: list[str], failed: list[str], warning: list[str]) -> list[str]:
    actions = []
    if missing:
        actions.append("generate_missing_evidence")
    if failed:
        actions.append("resolve_failed_gates")
    if warning:
        actions.append("review_warning_gates")
    if status == "blocked":
        actions.append("keep_live_disabled")
    return sorted(set(actions or ["manual_review"]))


def resolve_global_status(blockers: list[str], missing: list[str], gates: Mapping[str, Mapping[str, Any]], p1: list[str]) -> str:
    if blockers:
        return "blocked"
    if any("live_blocking" in item.lower() for item in p1):
        return "blocked"
    if any(gate["status"] == "blocked" for gate in gates.values()):
        return "blocked"
    if missing or any(gate["status"] in {"missing", "warning"} for gate in gates.values()):
        return "warning"
    return "ok"


def final_recommendation(status: str) -> str:
    if status == "ok":
        return "paper_shadow_ready_for_manual_go_no_go_review_live_remains_disabled"
    if status == "warning":
        return "continue_paper_shadow_and_close_missing_evidence_before_manual_go_no_go"
    return "blocked_keep_paper_shadow_only_until_findings_are_resolved"


def safety_payload(overrides: Mapping[str, Any] | None = None) -> dict[str, bool]:
    payload = {
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
    }
    if overrides:
        payload.update({key: bool(value) for key, value in overrides.items() if key in payload})
    return payload


def unsafe_flags(payload: Mapping[str, Any]) -> list[str]:
    flags = []
    if "paper_only" in payload and payload.get("paper_only") is not True:
        flags.append("paper_only")
    if "shadow_only" in payload and payload.get("shadow_only") is not True:
        flags.append("shadow_only")
    for flag in SAFE_FALSE_FLAGS:
        if payload.get(flag):
            flags.append(flag)
    return flags


def any_unsafe_flag(payload: Mapping[str, Any]) -> bool:
    return bool(unsafe_flags(payload))


def int_value(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def write_report(report: dict[str, Any], output_path: str | Path | None) -> None:
    if output_path is None:
        return
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def ensure_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def iso(value: datetime) -> str:
    return ensure_utc(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")
