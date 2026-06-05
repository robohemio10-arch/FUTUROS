from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smartcrypto.ops.paper_shadow_soak_report import monte_carlo_policy_summary


DEFAULT_REGISTRY_PATH = Path("data/models/registry/model_registry.json")
DEFAULT_TRAINER_REPORT_PATH = Path("data/reports/ai_shadow_incremental_trainer_report.json")
DEFAULT_PROMOTION_REPORT_PATH = Path("data/reports/model_registry_promotion_gate_report.json")
DEFAULT_DRIFT_REPORT_PATH = Path("data/reports/ai_shadow_drift_monitor_report.json")
DEFAULT_OUTCOMES_REPORT_PATH = Path("data/reports/ai_shadow_model_outcomes_report.json")
DEFAULT_FINANCIAL_REPORT_PATH = Path("data/reports/ai_shadow_financial_threshold_evaluation_report.json")
DEFAULT_ANTI_LEAKAGE_REPORT_PATH = Path("data/reports/phase23_anti_leakage_report.json")
DEFAULT_MONTE_CARLO_REPORT_PATH = Path("data/reports/monte_carlo_risk_simulation_report.json")
DEFAULT_MONTE_CARLO_RISK_BUDGET_POLICY_REPORT_PATH = Path("data/reports/monte_carlo_risk_budget_policy_report.json")
DEFAULT_BACKTEST_REPORT_PATH = Path("data/reports/event_driven_backtest_report.json")
DEFAULT_DATA_QUALITY_REPORT_PATH = Path("data/reports/data_quality_report.json")
DEFAULT_DATASET_MANIFEST_PATH = Path("data/reports/dataset_manifest.json")
DEFAULT_DECISIONS_JSONL_PATH = Path("data/reports/ai_shadow_model_decisions.jsonl")

DEFAULT_SOURCES = {
    "registry": DEFAULT_REGISTRY_PATH,
    "trainer_report": DEFAULT_TRAINER_REPORT_PATH,
    "promotion_report": DEFAULT_PROMOTION_REPORT_PATH,
    "drift_report": DEFAULT_DRIFT_REPORT_PATH,
    "outcomes_report": DEFAULT_OUTCOMES_REPORT_PATH,
    "financial_report": DEFAULT_FINANCIAL_REPORT_PATH,
    "anti_leakage_report": DEFAULT_ANTI_LEAKAGE_REPORT_PATH,
    "monte_carlo_report": DEFAULT_MONTE_CARLO_REPORT_PATH,
    "monte_carlo_risk_budget_policy_report": DEFAULT_MONTE_CARLO_RISK_BUDGET_POLICY_REPORT_PATH,
    "backtest_report": DEFAULT_BACKTEST_REPORT_PATH,
    "data_quality_report": DEFAULT_DATA_QUALITY_REPORT_PATH,
    "dataset_manifest": DEFAULT_DATASET_MANIFEST_PATH,
    "decisions_jsonl": DEFAULT_DECISIONS_JSONL_PATH,
}

SAFE_FALSE_FLAGS = (
    "live_trading_enabled",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "changes_risk",
)
FORBIDDEN_ACTION_LABELS = (
    "promote",
    "train",
    "apply threshold",
    "enable live",
    "send order",
    "change risk",
)
BLOCKED_STATUSES = {"blocked", "safety_alert", "error"}
WARNING_STATUSES = {"warning", "warn", "degraded"}
OPTIONAL_SOURCE_NAMES = {"decisions_jsonl", "outcomes_report", "monte_carlo_risk_budget_policy_report"}


def load_ai_governance_panel_state(
    *,
    source_paths: dict[str, str | Path | None] | None = None,
    strict: bool = False,
    decisions_tail: int = 200,
) -> dict[str, Any]:
    paths = build_source_paths(source_paths)
    sources = {name: load_source(name, path, decisions_tail=decisions_tail) for name, path in paths.items()}
    payloads = {name: source.get("payload") or {} for name, source in sources.items()}
    decisions = sources["decisions_jsonl"].get("rows") or []
    registry = summarize_registry(payloads["registry"])
    promotion = summarize_promotion_gate(payloads["promotion_report"])
    trainer = summarize_trainer(payloads["trainer_report"])
    drift = summarize_status_source(payloads["drift_report"], preferred_keys=("drift_status", "status"))
    financial = summarize_financial_thresholds(payloads["financial_report"])
    outcomes = summarize_status_source(payloads["outcomes_report"], preferred_keys=("outcome_tracking_status", "status"))
    anti_leakage = summarize_status_source(payloads["anti_leakage_report"], preferred_keys=("status",))
    monte_carlo = summarize_status_source(
        payloads["monte_carlo_report"],
        preferred_keys=("recommendation_status", "status"),
    )
    monte_carlo_policy = monte_carlo_policy_summary(
        payloads.get("monte_carlo_risk_budget_policy_report", {}),
        source_exists=sources.get("monte_carlo_risk_budget_policy_report", {}).get("exists", False),
    )
    backtest = summarize_status_source(payloads["backtest_report"], preferred_keys=("status",))
    data_quality = summarize_status_source(payloads["data_quality_report"], preferred_keys=("status",))
    dataset_manifest = summarize_status_source(payloads["dataset_manifest"], preferred_keys=("status",))
    latest_decision = latest_shadow_decision(decisions)
    safety_flags = collect_safety_flags(payloads)
    safety_alerts = unsafe_safety_flags(safety_flags)
    blocked_reasons = aggregate_blocked_reasons(
        sources=sources,
        payloads=payloads,
        promotion=promotion,
        drift=drift,
        anti_leakage=anti_leakage,
        data_quality=data_quality,
        safety_alerts=safety_alerts,
        monte_carlo_policy=monte_carlo_policy,
    )
    warnings = aggregate_warnings(
        sources=sources,
        trainer=trainer,
        dataset_manifest=dataset_manifest,
        monte_carlo=monte_carlo,
        backtest=backtest,
        financial=financial,
    )
    status = aggregate_status(blocked_reasons, warnings, sources, strict=strict)
    return {
        "status": status,
        "reason": "ok" if status == "ok" else ";".join(blocked_reasons or warnings or ["missing_data"]),
        "generated_at_utc": utc_timestamp(),
        "sources": sources,
        "registry": registry,
        "champion_model_id": registry["champion_model_id"],
        "champion_model_version": registry["champion_model_version"],
        "challengers": registry["challengers"],
        "promotion_gate": promotion,
        "promotion_status": promotion["promotion_status"],
        "blocked_gates": promotion["blocked_gates"],
        "rejection_reasons": promotion["rejection_reasons"],
        "trainer": trainer,
        "trainer_metrics": trainer["metrics"],
        "sample_warning": trainer["sample_warning"],
        "drift_status": drift["status"],
        "financial_threshold_recommendation": financial["recommendation"],
        "promotion_allowed": promotion["promotion_allowed"],
        "auto_promote": promotion["auto_promote"],
        "latest_shadow_decision": latest_decision,
        "latest_outcome_tracking_status": outcomes["status"],
        "anti_leakage_status": anti_leakage["status"],
        "monte_carlo_recommendation_status": monte_carlo["status"],
        "monte_carlo_risk_budget_policy_status": monte_carlo_policy["status"],
        "monte_carlo_risk_budget_policy_action": monte_carlo_policy["policy_action"],
        "monte_carlo_risk_treated": monte_carlo_policy["monte_carlo_risk_treated"] and normalize_status(monte_carlo["status"]) == "blocked",
        "no_trade_policy_present": monte_carlo_policy["no_trade_policy_present"],
        "live_release_allowed": False,
        "event_driven_backtest_status": backtest["status"],
        "data_quality_status": data_quality["status"],
        "dataset_manifest_status": dataset_manifest["status"],
        "outcomes": outcomes,
        "financial": financial,
        "monte_carlo": monte_carlo,
        "backtest": backtest,
        "data_quality": data_quality,
        "dataset_manifest": dataset_manifest,
        "safety_flags": safety_flags,
        "safety_alerts": safety_alerts,
        "blocked_reasons": blocked_reasons,
        "warnings": warnings,
        "missing_sources": sorted(name for name, source in sources.items() if source["status"] == "missing"),
        "is_read_only": True,
        "read_only": True,
        "forbidden_actions_present": [],
        "paper_only": safety_flags["paper_only"],
        "shadow_only": safety_flags["shadow_only"],
    }


def build_source_paths(overrides: dict[str, str | Path | None] | None) -> dict[str, Path]:
    result = dict(DEFAULT_SOURCES)
    for key, value in (overrides or {}).items():
        if key in result and value is not None:
            result[key] = Path(value)
    return {key: Path(value) for key, value in result.items()}


def load_source(name: str, path: Path, *, decisions_tail: int) -> dict[str, Any]:
    if name == "decisions_jsonl":
        return load_jsonl_source(name, path, tail=decisions_tail)
    return load_json_source(name, path)


def load_json_source(name: str, path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"name": name, "path": str(path), "exists": False, "status": "missing", "payload": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception as exc:
        return {
            "name": name,
            "path": str(path),
            "exists": True,
            "status": "blocked",
            "payload": {},
            "error": f"json_read_failed:{exc}",
        }
    if not isinstance(payload, dict):
        return {
            "name": name,
            "path": str(path),
            "exists": True,
            "status": "blocked",
            "payload": {},
            "error": "json_root_not_object",
        }
    return {"name": name, "path": str(path), "exists": True, "status": "ok", "payload": payload}


def load_jsonl_source(name: str, path: Path, *, tail: int) -> dict[str, Any]:
    if not path.exists():
        return {"name": name, "path": str(path), "exists": False, "status": "missing", "rows": []}
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception as exc:
        return {
            "name": name,
            "path": str(path),
            "exists": True,
            "status": "blocked",
            "rows": [],
            "error": f"jsonl_read_failed:{exc}",
        }
    for line in lines[-int(tail) :]:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return {"name": name, "path": str(path), "exists": True, "status": "ok", "rows": rows}


def summarize_registry(payload: dict[str, Any]) -> dict[str, Any]:
    challengers = payload.get("challengers")
    if not isinstance(challengers, list):
        challengers = payload.get("models") if isinstance(payload.get("models"), list) else []
    return {
        "registry_version": payload.get("registry_version"),
        "updated_at_utc": payload.get("updated_at_utc") or payload.get("updated_at"),
        "champion_model_id": payload.get("champion_model_id"),
        "champion_model_version": payload.get("champion_model_version"),
        "challengers": challengers,
        "rejected_promotions": payload.get("rejected_promotions") if isinstance(payload.get("rejected_promotions"), list) else [],
    }


def summarize_promotion_gate(payload: dict[str, Any]) -> dict[str, Any]:
    violations = list_values(payload, ("promotion_violations", "blocked_gates", "blocking_errors"))
    rejected = list_values(payload, ("rejection_reasons", "rejected_promotions", "warnings"))
    promotion_status = payload.get("promotion_status") or payload.get("status") or "missing"
    promotion_allowed = as_bool(payload.get("promotion_allowed"), default=False)
    auto_promote = as_bool(payload.get("auto_promote"), default=False)
    return {
        "status": normalize_status(payload.get("status")),
        "promotion_status": promotion_status,
        "promotion_allowed": promotion_allowed,
        "auto_promote": auto_promote,
        "blocked_gates": violations,
        "rejection_reasons": rejected,
        "formal_gate_approved": formal_gate_approved(payload),
    }


def summarize_trainer(payload: dict[str, Any]) -> dict[str, Any]:
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    class_balance = payload.get("class_balance") if isinstance(payload.get("class_balance"), dict) else {}
    return {
        "status": normalize_status(payload.get("status")),
        "model_id": payload.get("model_id"),
        "model_version": payload.get("model_version"),
        "input_rows": payload.get("input_rows"),
        "feature_columns": payload.get("feature_columns") if isinstance(payload.get("feature_columns"), list) else [],
        "target_column": payload.get("target_column"),
        "sample_warning": as_bool(payload.get("sample_warning"), default=False),
        "promotion_status": payload.get("promotion_status"),
        "auto_promote": as_bool(payload.get("auto_promote"), default=False),
        "metrics": metrics,
        "class_balance": class_balance,
    }


def summarize_status_source(payload: dict[str, Any], *, preferred_keys: tuple[str, ...]) -> dict[str, Any]:
    status = next((payload.get(key) for key in preferred_keys if payload.get(key) is not None), None)
    return {
        "status": normalize_status(status),
        "reason": payload.get("reason"),
        "payload": payload,
    }


def summarize_financial_thresholds(payload: dict[str, Any]) -> dict[str, Any]:
    recommendation = (
        payload.get("financial_threshold_recommendation")
        or payload.get("recommendation")
        or payload.get("recommended_threshold")
        or payload.get("status")
    )
    return {
        "status": normalize_status(payload.get("status")),
        "recommendation": recommendation,
        "payload": payload,
    }


def latest_shadow_decision(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return rows[-1]


def collect_safety_flags(payloads: dict[str, dict[str, Any]]) -> dict[str, Any]:
    flags: dict[str, Any] = {
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
    }
    for payload in payloads.values():
        merge_safety_flags(flags, payload)
    return flags


def merge_safety_flags(flags: dict[str, Any], payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        return
    safety = payload.get("safety_status") if isinstance(payload.get("safety_status"), dict) else {}
    for key in ("paper_only", "shadow_only"):
        if payload.get(key) is False or safety.get(key) is False:
            flags[key] = False
    for key in SAFE_FALSE_FLAGS:
        if payload.get(key) is True or safety.get(key) is True:
            flags[key] = True
    for value in payload.values():
        if isinstance(value, dict):
            merge_safety_flags(flags, value)


def unsafe_safety_flags(flags: dict[str, Any]) -> list[str]:
    alerts: list[str] = []
    if flags.get("paper_only") is not True:
        alerts.append("paper_only_not_true")
    if flags.get("shadow_only") is not True:
        alerts.append("shadow_only_not_true")
    for key in SAFE_FALSE_FLAGS:
        if flags.get(key) is True:
            alerts.append(f"{key}_true")
    return sorted(alerts)


def aggregate_blocked_reasons(
    *,
    sources: dict[str, dict[str, Any]],
    payloads: dict[str, dict[str, Any]],
    promotion: dict[str, Any],
    drift: dict[str, Any],
    anti_leakage: dict[str, Any],
    data_quality: dict[str, Any],
    safety_alerts: list[str],
    monte_carlo_policy: dict[str, Any],
) -> list[str]:
    reasons = [f"unsafe_safety_flag:{item}" for item in safety_alerts]
    reasons.extend(f"source_blocked:{name}" for name, source in sources.items() if source["status"] == "blocked")
    if promotion["auto_promote"]:
        reasons.append("auto_promote_true")
    if promotion["promotion_allowed"] and not promotion["formal_gate_approved"]:
        reasons.append("promotion_allowed_without_formal_gate")
    if normalize_status(drift["status"]) == "blocked":
        reasons.append("drift_status_blocked")
    if normalize_status(anti_leakage["status"]) == "blocked":
        reasons.append("anti_leakage_blocked")
    if normalize_status(data_quality["status"]) == "blocked":
        reasons.append("data_quality_blocked")
    for name, payload in payloads.items():
        if name in {"monte_carlo_report", "monte_carlo_risk_budget_policy_report"} and monte_carlo_policy["no_trade_policy_present"] and normalize_status(payload.get("status")) in BLOCKED_STATUSES:
            continue
        if name in OPTIONAL_SOURCE_NAMES and normalize_status(payload.get("status")) == "missing":
            continue
        if normalize_status(payload.get("status")) in BLOCKED_STATUSES:
            reasons.append(f"artifact_status_blocked:{name}")
    return sorted(set(reasons))


def aggregate_warnings(
    *,
    sources: dict[str, dict[str, Any]],
    trainer: dict[str, Any],
    dataset_manifest: dict[str, Any],
    monte_carlo: dict[str, Any],
    backtest: dict[str, Any],
    financial: dict[str, Any],
) -> list[str]:
    warnings = [
        f"missing_source:{name}"
        for name, source in sources.items()
        if source["status"] == "missing" and (name not in OPTIONAL_SOURCE_NAMES or normalize_status(financial["status"]) != "ok")
    ]
    if trainer["sample_warning"]:
        warnings.append("trainer_sample_warning")
    for name, summary in (
        ("dataset_manifest", dataset_manifest),
        ("monte_carlo", monte_carlo),
        ("event_driven_backtest", backtest),
    ):
        if normalize_status(summary["status"]) in WARNING_STATUSES:
            warnings.append(f"{name}_warning")
    return sorted(set(warnings))


def aggregate_status(
    blocked_reasons: list[str],
    warnings: list[str],
    sources: dict[str, dict[str, Any]],
    *,
    strict: bool,
) -> str:
    if blocked_reasons:
        return "blocked"
    missing = any(source["status"] == "missing" for source in sources.values())
    if strict and missing:
        return "blocked"
    if missing and len(warnings) == len([source for source in sources.values() if source["status"] == "missing"]):
        return "missing_data"
    if warnings:
        return "warning"
    return "ok"


def list_values(payload: dict[str, Any], keys: tuple[str, ...]) -> list[Any]:
    values: list[Any] = []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            values.extend(value)
        elif value not in (None, "", {}):
            values.append(value)
    return values


def formal_gate_approved(payload: dict[str, Any]) -> bool:
    if payload.get("formal_gate_approved") is True or payload.get("gate_approved") is True:
        return True
    status = normalize_status(payload.get("status"))
    promotion_status = str(payload.get("promotion_status") or "").strip().lower()
    return status == "ok" and promotion_status in {"approved", "approved_for_shadow", "promotion_approved"}


def normalize_status(value: Any) -> str:
    if value in (None, ""):
        return "missing"
    text = str(value).strip().lower()
    if text in {"ok", "passed", "valid", "clean"}:
        return "ok"
    if text in {"blocked", "block", "failed", "error", "invalid"}:
        return "blocked"
    if text in {"warning", "warn", "degraded"}:
        return "warning"
    return text


def as_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def render_ai_governance_panel(st_module: Any, state: dict[str, Any] | None = None) -> None:
    state = state or load_ai_governance_panel_state()
    st_module.subheader("Governança IA e Model Registry")
    st_module.caption("Painel read-only. Nao promove modelos, nao treina, nao altera thresholds e nao envia ordens.")

    if state["status"] == "blocked":
        st_module.error({"status": state["status"], "blocked_reasons": state["blocked_reasons"]})
    elif state["status"] in {"warning", "missing_data"}:
        st_module.warning({"status": state["status"], "warnings": state["warnings"]})
    else:
        st_module.success("Governança IA sem bloqueios nos artefatos disponíveis.")

    c1, c2, c3, c4 = st_module.columns(4)
    c1.metric("Status", state["status"])
    c2.metric("Champion", state.get("champion_model_id") or "none")
    c3.metric("Challengers", len(state.get("challengers") or []))
    c4.metric("Drift", state.get("drift_status") or "missing")

    st_module.write(
        {
            "champion_model_id": state.get("champion_model_id"),
            "champion_model_version": state.get("champion_model_version"),
            "promotion_status": state.get("promotion_status"),
            "promotion_allowed": state.get("promotion_allowed"),
            "auto_promote": state.get("auto_promote"),
            "sample_warning": state.get("sample_warning"),
            "financial_threshold_recommendation": state.get("financial_threshold_recommendation"),
            "latest_outcome_tracking_status": state.get("latest_outcome_tracking_status"),
            "anti_leakage_status": state.get("anti_leakage_status"),
            "monte_carlo_recommendation_status": state.get("monte_carlo_recommendation_status"),
            "event_driven_backtest_status": state.get("event_driven_backtest_status"),
            "data_quality_status": state.get("data_quality_status"),
            "dataset_manifest_status": state.get("dataset_manifest_status"),
            "safety_flags": state.get("safety_flags"),
        }
    )
    st_module.subheader("Trainer metrics")
    st_module.json(state.get("trainer_metrics") or {})
    st_module.subheader("Promotion gate")
    st_module.json(
        {
            "blocked_gates": state.get("blocked_gates"),
            "rejection_reasons": state.get("rejection_reasons"),
        }
    )
    st_module.subheader("Última decisão shadow")
    st_module.json(state.get("latest_shadow_decision") or {})
    st_module.subheader("Fontes")
    st_module.json({name: {"path": item["path"], "status": item["status"], "exists": item["exists"]} for name, item in state["sources"].items()})
