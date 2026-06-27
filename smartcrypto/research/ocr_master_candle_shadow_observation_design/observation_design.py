"""Research-only shadow observation design for OCR Master + candle OOS survivors.

This module converts the previous positive-rule OOS survivors into a formal
observation contract.  It deliberately stops at design-time evidence: it does
not register rules, does not apply rules, does not write runtime state, and does
not alter any trading surface.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "ocr_master_candle_shadow_observation_design_v1"
OBSERVATION_CONTRACT_VERSION = "shadow_observation_contract_v1"
PROJECT_NAME = "SMART FUTUROS"
DECISION_RESEARCH = "MANTER_EM_RESEARCH"
DEFAULT_OOS_REPORT = Path("data/reports/ocr_master_candle_positive_rule_oos_validation_v1.json")
DEFAULT_OUTPUT_REPORT = Path("data/reports/ocr_master_candle_shadow_observation_design_v1.json")

OBSERVATION_FIELDS = [
    "survivor_rule_id",
    "survivor_expression",
    "dimensions",
    "values",
    "would_allow",
    "would_block",
    "opportunity_score",
    "expected_value_delta",
    "shadow_observation_reason",
    "operational_authority",
    "can_apply_to_freqtrade",
    "can_apply_to_risk_manager",
    "can_promote_rules",
]

WOULD_ALLOW_SEMANTICS = (
    "Hypothetical boolean showing that a survivor rule would allow a shadow "
    "observation row if the same slice condition matched. It is not an order "
    "permission, not a paper selector, and not a live signal."
)
WOULD_BLOCK_SEMANTICS = (
    "Hypothetical boolean showing that the observation contract would block the "
    "row from the survivor-observation set. For OOS survivors this remains false; "
    "non-matches are simply outside the observation cohort."
)
OPPORTUNITY_SCORE_CONTRACT = (
    "Deterministic normalized score in [0, 1] computed from research-only OOS "
    "evidence: pass ratio, positive expected-value delta, profit factor and sample "
    "support. It has no operational authority."
)
EXPECTED_VALUE_DELTA_CONTRACT = (
    "Research-only delta comparing survivor OOS mean PnL against fold baseline "
    "mean PnL. It is descriptive evidence only and cannot update Freqtrade, "
    "RiskManager, Qlib, IA Shadow, models, registry or active signals."
)

FORBIDDEN_ACTIONS = [
    "alterar Freqtrade",
    "alterar RiskManager",
    "alterar Qlib runtime",
    "alterar IA Shadow runtime",
    "registrar ou promover shadow rule",
    "aplicar shadow rule",
    "promover modelo",
    "executar treino operacional",
    "habilitar live ou canary",
    "enviar ordem real",
    "usar exchange privada",
    "escrever runtime, sinais ativos, modelos, registry ou configuração operacional",
]

SAFETY_FLAGS: dict[str, bool] = {
    "research_only": True,
    "read_only": True,
    "paper_only": True,
    "shadow_only": True,
    "operational_authority": False,
    "release_authority": False,
    "readiness_release_authority": False,
    "live_trading_enabled": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "changes_risk": False,
    "changes_model": False,
    "updates_freqtrade": False,
    "updates_risk_manager": False,
    "updates_qlib_runtime": False,
    "updates_ai_shadow_runtime": False,
    "can_apply_to_freqtrade": False,
    "can_apply_to_risk_manager": False,
    "runs_training": False,
    "registers_shadow_rules": False,
    "registers_candidate_rules": False,
    "applies_shadow_rules": False,
    "applies_feedback_to_ai_shadow": False,
    "can_promote_rules": False,
    "can_promote_model": False,
    "ready_for_candidate_registry": False,
    "paper_observation_allowed": False,
    "remediation_application_allowed": False,
    "executes_orchestrator": False,
    "executes_scheduler": False,
    "executes_stage_builders": False,
    "writes_data": False,
    "writes_runtime": False,
    "writes_reports": False,
    "writes_parquet": False,
    "writes_sqlite": False,
    "writes_runtime_by_default": False,
}


@dataclass(frozen=True)
class LoadedSurvivors:
    survivors: list[dict[str, Any]]
    source_mode: str
    source_path: str | None
    source_sha256: str | None
    source_status: str
    source_warning: str | None = None


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _project_relative(path: Path, project_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def _sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _safe_float(value: object, *, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, str):
        if value.lower() == "inf":
            return float("inf")
        if value.lower() == "-inf":
            return float("-inf")
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(numeric):
        return default
    return numeric


def _bounded(value: float, *, low: float = 0.0, high: float = 1.0) -> float:
    if math.isnan(value):
        return low
    return min(high, max(low, value))


def _rounded_or_none(value: float | None) -> float | None:
    if value is None or math.isnan(value) or math.isinf(value):
        return None
    return round(float(value), 10)


def _profit_factor_score(value: object) -> float:
    numeric = _safe_float(value)
    if math.isinf(numeric) and numeric > 0:
        return 1.0
    # PF=1 is neutral. PF>=3 is considered saturated for this descriptive score.
    return _bounded((numeric - 1.0) / 2.0)


def _support_score(trade_count: int) -> float:
    if trade_count <= 0:
        return 0.0
    # Saturates near 120 trades without making sample size an operational gate.
    return _bounded(math.log1p(trade_count) / math.log1p(120))


def _expected_value_delta(candidate: Mapping[str, Any]) -> float:
    fold_results = candidate.get("fold_results")
    deltas: list[float] = []
    if isinstance(fold_results, Sequence) and not isinstance(fold_results, (str, bytes)):
        for fold in fold_results:
            if isinstance(fold, Mapping):
                deltas.append(_safe_float(fold.get("mean_pnl_lift")))
    if deltas:
        return round(sum(deltas) / len(deltas), 10)

    aggregate = candidate.get("aggregate_oos_metrics")
    insample = candidate.get("insample_candidate")
    if isinstance(aggregate, Mapping) and isinstance(insample, Mapping):
        return round(
            _safe_float(aggregate.get("mean_pnl")) - _safe_float(insample.get("baseline_mean_pnl")),
            10,
        )
    return 0.0


def _opportunity_score(candidate: Mapping[str, Any], ev_delta: float) -> float:
    aggregate = candidate.get("aggregate_oos_metrics") if isinstance(candidate.get("aggregate_oos_metrics"), Mapping) else {}
    pass_ratio = _bounded(_safe_float(candidate.get("oos_pass_ratio")))
    pf_component = _profit_factor_score(aggregate.get("profit_factor"))
    trade_count = int(_safe_float(aggregate.get("trade_count")))
    support_component = _support_score(trade_count)
    ev_component = _bounded((ev_delta + 0.25) / 1.25)
    score = (0.35 * pass_ratio) + (0.30 * pf_component) + (0.20 * ev_component) + (0.15 * support_component)
    return round(_bounded(score), 10)


def _candidate_identifier(candidate: Mapping[str, Any], index: int) -> str:
    value = candidate.get("candidate_id") or candidate.get("survivor_rule_id")
    if value:
        return str(value)
    expression = str(candidate.get("expression") or candidate.get("survivor_expression") or f"survivor_{index}")
    digest = hashlib.sha256(expression.encode("utf-8")).hexdigest()[:12]
    return f"survivor_{digest}"


def _candidate_expression(candidate: Mapping[str, Any]) -> str:
    value = candidate.get("expression") or candidate.get("survivor_expression")
    if value:
        return str(value)
    dimensions = candidate.get("dimensions", [])
    values = candidate.get("values", [])
    if isinstance(dimensions, Sequence) and isinstance(values, Sequence):
        pairs = zip(dimensions, values, strict=False)
        expression = " AND ".join(f"{dimension} == '{value}'" for dimension, value in pairs)
        if expression:
            return expression
    return "UNSPECIFIED_RESEARCH_SURVIVOR_EXPRESSION"


def _normalize_survivor(candidate: Mapping[str, Any], index: int) -> dict[str, Any]:
    ev_delta = _expected_value_delta(candidate)
    score = _opportunity_score(candidate, ev_delta)
    aggregate = candidate.get("aggregate_oos_metrics") if isinstance(candidate.get("aggregate_oos_metrics"), Mapping) else {}
    record = {
        "survivor_rule_id": _candidate_identifier(candidate, index),
        "survivor_expression": _candidate_expression(candidate),
        "dimensions": list(candidate.get("dimensions", [])) if isinstance(candidate.get("dimensions", []), Sequence) else [],
        "values": list(candidate.get("values", [])) if isinstance(candidate.get("values", []), Sequence) else [],
        "would_allow": True,
        "would_block": False,
        "opportunity_score": score,
        "expected_value_delta": _rounded_or_none(ev_delta),
        "shadow_observation_reason": "survived_positive_rule_oos_research_gate_but_remains_non_operational",
        "oos_pass_ratio": _rounded_or_none(_safe_float(candidate.get("oos_pass_ratio"))),
        "folds_evaluated": int(_safe_float(candidate.get("folds_evaluated"))),
        "folds_passed": int(_safe_float(candidate.get("folds_passed"))),
        "aggregate_oos_metrics": dict(aggregate),
        "research_only": True,
        "read_only": True,
        "operational_authority": False,
        "paper_observation_allowed": False,
        "ready_for_candidate_registry": False,
        "can_apply_to_freqtrade": False,
        "can_apply_to_risk_manager": False,
        "can_promote_rules": False,
        "registers_shadow_rules": False,
        "applies_shadow_rules": False,
    }
    return record


def build_observation_records(survivors: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Convert OOS survivor dictionaries into deterministic observation records."""

    records = [_normalize_survivor(candidate, index) for index, candidate in enumerate(survivors, start=1)]
    records.sort(
        key=lambda item: (
            float(item.get("opportunity_score") or 0.0),
            float(item.get("expected_value_delta") or 0.0),
            str(item.get("survivor_rule_id") or ""),
        ),
        reverse=True,
    )
    return records


def _load_survivors_from_report(path: Path, root: Path) -> LoadedSurvivors:
    if not path.exists():
        return LoadedSurvivors(
            survivors=[],
            source_mode="runtime_report_missing",
            source_path=_project_relative(path, root),
            source_sha256=None,
            source_status="missing",
            source_warning=f"previous_oos_report_missing:{_project_relative(path, root)}",
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return LoadedSurvivors(
            survivors=[],
            source_mode="runtime_report_invalid_json",
            source_path=_project_relative(path, root),
            source_sha256=_sha256_file(path),
            source_status="invalid_json",
            source_warning=f"previous_oos_report_invalid_json:{exc}",
        )
    shortlist = payload.get("oos_shortlist", [])
    if not isinstance(shortlist, list):
        return LoadedSurvivors(
            survivors=[],
            source_mode="runtime_report_schema_invalid",
            source_path=_project_relative(path, root),
            source_sha256=_sha256_file(path),
            source_status="schema_invalid",
            source_warning="previous_oos_report_oos_shortlist_not_list",
        )
    survivors = [item for item in shortlist if isinstance(item, Mapping)]
    return LoadedSurvivors(
        survivors=[dict(item) for item in survivors],
        source_mode="runtime_report_read_only",
        source_path=_project_relative(path, root),
        source_sha256=_sha256_file(path),
        source_status="ok",
        source_warning=None,
    )


def _load_survivors(
    *,
    project_root: Path,
    allow_runtime_read: bool,
    oos_report_path: str | Path | None,
    survivor_results: Sequence[Mapping[str, Any]] | None,
) -> LoadedSurvivors:
    if survivor_results is not None:
        return LoadedSurvivors(
            survivors=[dict(item) for item in survivor_results],
            source_mode="in_memory_survivors",
            source_path=None,
            source_sha256=None,
            source_status="ok",
            source_warning=None,
        )
    if not allow_runtime_read:
        return LoadedSurvivors(
            survivors=[],
            source_mode="no_runtime_rows_loaded",
            source_path=None,
            source_sha256=None,
            source_status="blocked_by_default",
            source_warning="runtime_read_not_allowed_by_default",
        )
    path = Path(oos_report_path) if oos_report_path is not None else DEFAULT_OOS_REPORT
    if not path.is_absolute():
        path = project_root / path
    return _load_survivors_from_report(path, project_root)


def _build_gate_matrix(*, loaded: LoadedSurvivors, survivor_count: int, write_requested: bool) -> list[dict[str, Any]]:
    source_available = loaded.source_status == "ok"
    return [
        {
            "gate_id": "research_only_contract",
            "gate_name": "Research-only contract preserved",
            "severity": "critical",
            "passed": True,
            "evidence": "research_only=True; operational_authority=False",
        },
        {
            "gate_id": "survivor_source_available",
            "gate_name": "OOS survivor source is available when explicitly supplied or read",
            "severity": "high",
            "passed": source_available,
            "evidence": f"source_mode={loaded.source_mode}; source_status={loaded.source_status}; survivor_count={survivor_count}",
        },
        {
            "gate_id": "observation_contract_materialized",
            "gate_name": "Observation contract fields are materialized",
            "severity": "high",
            "passed": survivor_count > 0,
            "evidence": f"observation_fields={len(OBSERVATION_FIELDS)}; survivor_count={survivor_count}",
        },
        {
            "gate_id": "no_registry_or_rule_application",
            "gate_name": "No registry insertion and no shadow rule application",
            "severity": "critical",
            "passed": True,
            "evidence": "registers_shadow_rules=false; applies_shadow_rules=false; can_promote_rules=false",
        },
        {
            "gate_id": "runtime_surfaces_unchanged",
            "gate_name": "Freqtrade, RiskManager, Qlib and IA Shadow runtime remain unchanged",
            "severity": "critical",
            "passed": True,
            "evidence": "updates_freqtrade=false; updates_risk_manager=false; updates_qlib_runtime=false; updates_ai_shadow_runtime=false",
        },
        {
            "gate_id": "write_scope_research_only",
            "gate_name": "Write scope is restricted to optional research report",
            "severity": "high",
            "passed": True,
            "evidence": f"write_requested={write_requested}; writes_runtime=false; writes_sqlite=false; writes_parquet=false",
        },
        {
            "gate_id": "paper_observation_still_blocked",
            "gate_name": "Paper observation remains blocked",
            "severity": "critical",
            "passed": True,
            "evidence": "paper_observation_allowed=false; ready_for_shadow_observation=false",
        },
    ]


def _summarize_gates(gates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    failed = [gate for gate in gates if not gate.get("passed")]
    critical_failed = [gate for gate in failed if gate.get("severity") == "critical"]
    return {
        "gate_count": len(gates),
        "passed_gate_count": len(gates) - len(failed),
        "failed_gate_count": len(failed),
        "failed_gate_ids": [str(gate["gate_id"]) for gate in failed],
        "critical_failed_gate_ids": [str(gate["gate_id"]) for gate in critical_failed],
    }


def _reason(*, loaded: LoadedSurvivors, survivor_count: int) -> str:
    if loaded.source_status != "ok":
        if loaded.source_status == "blocked_by_default":
            return "shadow_observation_design_requires_explicit_survivor_source_or_runtime_read"
        return "shadow_observation_design_blocked_missing_previous_oos_survivor_source"
    if survivor_count <= 0:
        return "shadow_observation_design_completed_no_oos_survivors"
    return "shadow_observation_design_materialized_research_only_no_operational_authority"


def _json_default(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return str(value)


def build_shadow_observation_design_report(
    *,
    project_root: str | Path,
    allow_runtime_read: bool = False,
    oos_report: str | Path | None = None,
    survivor_results: Sequence[Mapping[str, Any]] | None = None,
    write: bool = False,
    no_write: bool = True,
) -> dict[str, Any]:
    """Build the non-operational shadow-observation design report."""

    root = Path(project_root)
    write_requested = bool(write and not no_write)
    loaded = _load_survivors(
        project_root=root,
        allow_runtime_read=allow_runtime_read,
        oos_report_path=oos_report,
        survivor_results=survivor_results,
    )
    observation_records = build_observation_records(loaded.survivors)
    survivor_count = len(observation_records)
    gates = _build_gate_matrix(loaded=loaded, survivor_count=survivor_count, write_requested=write_requested)
    gate_summary = _summarize_gates(gates)

    safety_flags = dict(SAFETY_FLAGS)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "project_name": PROJECT_NAME,
        "generated_at_utc": _utc_now_iso(),
        "project_root": str(project_root),
        "status": "blocked",
        "reason": _reason(loaded=loaded, survivor_count=survivor_count),
        "decision": DECISION_RESEARCH,
        "input_mode": loaded.source_mode,
        "allow_runtime_read": allow_runtime_read,
        "write_requested": write_requested,
        "write_performed": False,
        "output_path": None,
        "observation_contract_version": OBSERVATION_CONTRACT_VERSION,
        "observation_fields": list(OBSERVATION_FIELDS),
        "would_allow_semantics": WOULD_ALLOW_SEMANTICS,
        "would_block_semantics": WOULD_BLOCK_SEMANTICS,
        "opportunity_score_contract": OPPORTUNITY_SCORE_CONTRACT,
        "expected_value_delta_contract": EXPECTED_VALUE_DELTA_CONTRACT,
        "forbidden_actions": list(FORBIDDEN_ACTIONS),
        "previous_oos_source": {
            "source_mode": loaded.source_mode,
            "source_path": loaded.source_path,
            "source_sha256": loaded.source_sha256,
            "source_status": loaded.source_status,
            "source_warning": loaded.source_warning,
        },
        "survivor_count": survivor_count,
        "observation_record_count": survivor_count,
        "observation_records": observation_records,
        "ready_for_shadow_observation": False,
        "paper_observation_allowed": False,
        "ready_for_candidate_registry": False,
        "registers_shadow_rules": False,
        "applies_shadow_rules": False,
        "can_apply_to_freqtrade": False,
        "can_apply_to_risk_manager": False,
        "can_promote_rules": False,
        "can_promote_model": False,
        "gate_matrix": gates,
        "gate_summary": gate_summary,
        "safety_flags": safety_flags,
        **safety_flags,
    }

    if write_requested:
        output_path = root / DEFAULT_OUTPUT_REPORT
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, default=_json_default) + "\n",
            encoding="utf-8",
        )
        report["write_performed"] = True
        report["writes_reports"] = True
        report["safety_flags"] = {**safety_flags, "writes_reports": True}
        report["output_path"] = _project_relative(output_path, root)

    return report
