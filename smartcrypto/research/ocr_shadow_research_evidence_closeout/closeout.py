"""Research-only closeout for the OCR Shadow Research evidence cycle.

The closeout consolidates research evidence and blockers from the OCR shadow
research cycle. It deliberately keeps the final decision in research and never
authorizes paper observation, rule promotion or runtime integration.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence


SCHEMA_VERSION = "ocr_shadow_research_evidence_closeout_v1"
PROJECT_NAME = "SMART FUTUROS"
CYCLE_NAME = "OCR Shadow Research"
DECISION_RESEARCH = "MANTER_EM_RESEARCH"
DEFAULT_OUTPUT_REPORT = Path("data/reports/ocr_shadow_research_evidence_closeout_v1.json")
DEFAULT_MARKDOWN_REPORT = Path("data/reports/ocr_shadow_research_evidence_closeout_v1.md")

EvidenceKey = Literal[
    "oos_validation",
    "observation_design",
    "observation_replay",
    "paper_attribution",
    "readiness_gate",
]

EVIDENCE_LABELS: dict[EvidenceKey, str] = {
    "oos_validation": "OCR Master Candle Positive Rule OOS Validation",
    "observation_design": "OCR Master Candle Shadow Observation Design",
    "observation_replay": "OCR Master Candle Shadow Observation Replay",
    "paper_attribution": "Paper Closed Trades Shadow Rule Attribution",
    "readiness_gate": "Paper Shadow Observation Readiness Gate",
}

SAFETY_FLAGS: dict[str, bool] = {
    "research_only": True,
    "read_only": True,
    "paper_only": True,
    "shadow_only": True,
    "operational_authority": False,
    "paper_observation_allowed": False,
    "ready_for_shadow_observation": False,
    "can_apply_to_freqtrade": False,
    "can_apply_to_risk_manager": False,
    "can_promote_rules": False,
    "can_promote_model": False,
    "sends_orders": False,
    "changes_risk": False,
    "changes_model": False,
    "exchange_private_access": False,
    "updates_freqtrade": False,
    "updates_risk_manager": False,
    "updates_qlib_runtime": False,
    "updates_ai_shadow_runtime": False,
    "registers_shadow_rules": False,
    "applies_shadow_rules": False,
    "writes_runtime": False,
    "writes_sqlite": False,
    "writes_parquet": False,
    "writes_data_by_default": False,
}

FORBIDDEN_NEXT_ACTIONS = [
    "ativar paper observer",
    "promover regra",
    "registrar regra operacional",
    "alterar runtime",
    "alterar RiskManager",
    "alterar Freqtrade",
    "alterar Qlib runtime",
    "alterar IA Shadow runtime",
    "alterar modelos",
    "alterar configs",
    "alterar sinais ativos",
    "escrever SQLite",
    "escrever Parquet operacional",
    "enviar ordens",
    "acessar exchange privada",
]

REQUIRED_TOP_LEVEL_FIELDS = [
    "status",
    "reason",
    "decision",
    "research_only",
    "read_only",
    "paper_only",
    "shadow_only",
    "operational_authority",
    "input_mode",
    "closeout_status",
    "closeout_decision",
    "cycle_name",
    "evidence_sources_required",
    "evidence_sources_present",
    "evidence_sources_missing",
    "evidence_summary",
    "blocker_summary",
    "readiness_snapshot",
    "paper_observation_allowed",
    "ready_for_shadow_observation",
    "can_apply_to_freqtrade",
    "can_apply_to_risk_manager",
    "can_promote_rules",
    "can_promote_model",
    "recommended_next_action",
    "forbidden_next_actions",
    "gate_summary",
    "safety_flags",
    "write_performed",
]


@dataclass(frozen=True)
class LoadedCloseoutInputs:
    reports: dict[EvidenceKey, dict[str, Any]]
    input_mode: str
    source_status: str
    source_reason: str
    source_paths: dict[str, str | None]
    source_sha256: dict[str, str | None]


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


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _safe_float(value: object, *, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(numeric) or math.isinf(numeric):
        return default
    return numeric


def _safe_int(value: object) -> int:
    return int(_safe_float(value, default=0.0))


def _round(value: float | None) -> float | None:
    if value is None or math.isnan(value) or math.isinf(value):
        return None
    return round(float(value), 10)


def _resolve_path(root: Path, value: str | Path | None) -> Path | None:
    if value is None:
        return None
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    return (root / path).resolve()


def load_closeout_inputs(
    *,
    project_root: str | Path,
    allow_runtime_read: bool = False,
    oos_validation_report: str | Path | None = None,
    shadow_observation_design_report: str | Path | None = None,
    shadow_observation_replay_report: str | Path | None = None,
    paper_closed_trades_attribution_report: str | Path | None = None,
    readiness_gate_report: str | Path | None = None,
    report_payloads: Mapping[str, Mapping[str, Any]] | None = None,
) -> LoadedCloseoutInputs:
    """Load closeout inputs only from memory or explicit local reports."""

    root = Path(project_root).resolve()
    if report_payloads is not None:
        reports: dict[EvidenceKey, dict[str, Any]] = {}
        for key in EVIDENCE_LABELS:
            payload = report_payloads.get(key)
            if isinstance(payload, Mapping):
                reports[key] = dict(payload)
        return LoadedCloseoutInputs(
            reports=reports,
            input_mode="in_memory_closeout_inputs",
            source_status="ok" if len(reports) == len(EVIDENCE_LABELS) else "blocked",
            source_reason="in_memory_inputs_supplied" if len(reports) == len(EVIDENCE_LABELS) else "missing_required_sources",
            source_paths={key: None for key in EVIDENCE_LABELS},
            source_sha256={key: None for key in EVIDENCE_LABELS},
        )
    if not allow_runtime_read:
        return LoadedCloseoutInputs(
            reports={},
            input_mode="no_runtime_rows_loaded",
            source_status="blocked",
            source_reason="runtime_read_not_allowed_by_default",
            source_paths={key: None for key in EVIDENCE_LABELS},
            source_sha256={key: None for key in EVIDENCE_LABELS},
        )

    requested = {
        "oos_validation": _resolve_path(root, oos_validation_report),
        "observation_design": _resolve_path(root, shadow_observation_design_report),
        "observation_replay": _resolve_path(root, shadow_observation_replay_report),
        "paper_attribution": _resolve_path(root, paper_closed_trades_attribution_report),
        "readiness_gate": _resolve_path(root, readiness_gate_report),
    }
    source_paths = {
        key: _project_relative(path, root) if path is not None else None for key, path in requested.items()
    }
    if any(path is None for path in requested.values()):
        return LoadedCloseoutInputs(
            reports={},
            input_mode="runtime_read_requested",
            source_status="blocked",
            source_reason="missing_required_sources",
            source_paths=source_paths,
            source_sha256={key: None for key in EVIDENCE_LABELS},
        )
    missing = [path for path in requested.values() if path is not None and not path.exists()]
    if missing:
        return LoadedCloseoutInputs(
            reports={},
            input_mode="runtime_read_requested",
            source_status="blocked",
            source_reason="source_path_missing",
            source_paths=source_paths,
            source_sha256={key: _sha256_file(path) if path is not None else None for key, path in requested.items()},
        )

    reports: dict[EvidenceKey, dict[str, Any]] = {}
    try:
        for key, path in requested.items():
            if path is None:
                continue
            payload = _read_json(path)
            if not isinstance(payload, Mapping):
                return LoadedCloseoutInputs(
                    reports={},
                    input_mode="runtime_read_requested",
                    source_status="blocked",
                    source_reason=f"invalid_report_payload:{key}",
                    source_paths=source_paths,
                    source_sha256={name: _sha256_file(source) if source is not None else None for name, source in requested.items()},
                )
            reports[key] = dict(payload)
    except (OSError, json.JSONDecodeError) as exc:
        return LoadedCloseoutInputs(
            reports={},
            input_mode="runtime_read_requested",
            source_status="blocked",
            source_reason=f"source_read_failed:{type(exc).__name__}",
            source_paths=source_paths,
            source_sha256={key: _sha256_file(path) if path is not None else None for key, path in requested.items()},
        )

    return LoadedCloseoutInputs(
        reports=reports,
        input_mode="runtime_read_requested",
        source_status="ok",
        source_reason="sources_loaded_read_only",
        source_paths=source_paths,
        source_sha256={key: _sha256_file(path) if path is not None else None for key, path in requested.items()},
    )


def compute_closeout(reports: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Compute deterministic closeout summary from research evidence reports."""

    evidence_summary = [_build_evidence_summary_row(key, reports.get(key)) for key in EVIDENCE_LABELS]
    evidence_missing = [row["evidence_key"] for row in evidence_summary if not row["present"]]
    safety_blockers: list[str] = []
    evidence_blockers: list[str] = []
    warnings: list[str] = []
    for row in evidence_summary:
        safety_blockers.extend(row["safety_blockers"])
        evidence_blockers.extend(row["evidence_blockers"])
        warnings.extend(row["warnings"])

    readiness_snapshot = _readiness_snapshot(reports.get("readiness_gate", {}))
    readiness_blockers = readiness_snapshot.get("readiness_blockers")
    if isinstance(readiness_blockers, list):
        evidence_blockers.extend(f"readiness_gate:{item}" for item in readiness_blockers)

    blockers = sorted(set([*safety_blockers, *evidence_blockers, *[f"missing:{item}" for item in evidence_missing]]))
    closeout_status = _closeout_status(evidence_missing=evidence_missing, safety_blockers=safety_blockers, evidence_blockers=evidence_blockers)
    return {
        "closeout_status": closeout_status,
        "closeout_decision": DECISION_RESEARCH,
        "cycle_name": CYCLE_NAME,
        "evidence_sources_required": len(EVIDENCE_LABELS),
        "evidence_sources_present": sum(1 for row in evidence_summary if row["present"]),
        "evidence_sources_missing": evidence_missing,
        "evidence_summary": evidence_summary,
        "blocker_summary": {
            "blocker_count": len(blockers),
            "blockers": blockers,
            "safety_blockers": sorted(set(safety_blockers)),
            "evidence_blockers": sorted(set(evidence_blockers)),
            "warnings": sorted(set(warnings)),
        },
        "readiness_snapshot": readiness_snapshot,
        "recommended_next_action": _recommended_next_action(evidence_missing, blockers),
        "forbidden_next_actions": list(FORBIDDEN_NEXT_ACTIONS),
        "gate_summary": {
            "decision": DECISION_RESEARCH,
            "closeout_decision": DECISION_RESEARCH,
            "closeout_status": closeout_status,
            "paper_observation_allowed": False,
            "ready_for_shadow_observation": False,
            "operational_authority": False,
            "can_apply_to_freqtrade": False,
            "can_apply_to_risk_manager": False,
            "can_promote_rules": False,
            "can_promote_model": False,
            "sends_orders": False,
            "changes_risk": False,
            "writes_runtime": False,
            "result_can_be_used_for_operations": False,
        },
    }


def _build_evidence_summary_row(key: EvidenceKey, payload: Mapping[str, Any] | None) -> dict[str, Any]:
    present = isinstance(payload, Mapping) and bool(payload)
    if not present:
        return {
            "evidence_key": key,
            "label": EVIDENCE_LABELS[key],
            "present": False,
            "schema_version": None,
            "status": "missing",
            "reason": "missing",
            "decision": None,
            "core_metrics": {},
            "safety_checks": [],
            "safety_blockers": [],
            "evidence_blockers": [f"{key}_missing"],
            "warnings": [],
        }

    assert payload is not None
    safety_checks = [
        _check_field(key, payload, "decision", DECISION_RESEARCH),
        _check_field(key, payload, "operational_authority", False),
        _check_field(key, payload, "sends_orders", False),
        _check_field(key, payload, "changes_risk", False),
        _check_field(key, payload, "can_promote_rules", False),
        _check_field(key, payload, "can_apply_to_freqtrade", False),
        _check_field(key, payload, "can_apply_to_risk_manager", False),
        _check_field(key, payload, "writes_runtime", False),
    ]
    safety_blockers = [str(check["blocker"]) for check in safety_checks if not check["passed"]]
    evidence_blockers = _evidence_blockers(key, payload)
    warnings: list[str] = []
    if payload.get("status") not in {"blocked", "warning", "ok"}:
        warnings.append(f"{key}_unexpected_status:{payload.get('status')}")
    return {
        "evidence_key": key,
        "label": EVIDENCE_LABELS[key],
        "present": True,
        "schema_version": payload.get("schema_version"),
        "status": payload.get("status"),
        "reason": payload.get("reason"),
        "decision": payload.get("decision"),
        "core_metrics": _core_metrics(key, payload),
        "safety_checks": safety_checks,
        "safety_blockers": safety_blockers,
        "evidence_blockers": evidence_blockers,
        "warnings": warnings,
    }


def _check_field(key: EvidenceKey, payload: Mapping[str, Any], field: str, expected: object) -> dict[str, Any]:
    actual = _field_value(payload, field)
    passed = actual == expected
    return {
        "field": field,
        "expected": expected,
        "actual": actual,
        "passed": passed,
        "blocker": None if passed else f"{key}_{field}_violates_closeout_contract",
    }


def _field_value(payload: Mapping[str, Any], field: str) -> object:
    if field in payload:
        return payload.get(field)
    safety_flags = payload.get("safety_flags")
    if isinstance(safety_flags, Mapping) and field in safety_flags:
        return safety_flags.get(field)
    return None


def _core_metrics(key: EvidenceKey, payload: Mapping[str, Any]) -> dict[str, Any]:
    if key == "oos_validation":
        return {"oos_survivor_count": _extract_oos_survivor_count(payload)}
    if key == "observation_design":
        return {
            "design_contract_status": "present"
            if payload.get("observation_contract_version") or payload.get("observation_fields")
            else "missing",
            "observation_record_count": _safe_int(payload.get("observation_record_count") or payload.get("survivor_count")),
        }
    if key == "observation_replay":
        metrics = payload.get("replay_metrics")
        return {
            "replay_trade_count": _safe_int(metrics.get("replay_trade_count") if isinstance(metrics, Mapping) else payload.get("replay_trade_count")),
            "would_allow_count": _safe_int(metrics.get("would_allow_count") if isinstance(metrics, Mapping) else payload.get("would_allow_count")),
            "would_block_count": _safe_int(metrics.get("would_block_count") if isinstance(metrics, Mapping) else payload.get("would_block_count")),
        }
    if key == "paper_attribution":
        return {
            "closed_trade_count": _safe_int(payload.get("closed_trade_count")),
            "attributed_trade_count": _safe_int(payload.get("attributed_trade_count")),
            "would_allow_count": _safe_int(payload.get("would_allow_count")),
            "would_block_count": _safe_int(payload.get("would_block_count")),
        }
    if key == "readiness_gate":
        return {
            "readiness_score": _round(_safe_float(payload.get("readiness_score"))),
            "readiness_level": payload.get("readiness_level"),
            "readiness_blocker_count": len(payload.get("readiness_blockers") or []),
        }
    return {}


def _evidence_blockers(key: EvidenceKey, payload: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    metrics = _core_metrics(key, payload)
    if key == "oos_validation" and _safe_int(metrics.get("oos_survivor_count")) <= 0:
        blockers.append("oos_survivors_absent")
    if key == "observation_design" and metrics.get("design_contract_status") != "present":
        blockers.append("design_contract_absent")
    if key == "observation_replay" and _safe_int(metrics.get("replay_trade_count")) <= 0:
        blockers.append("replay_report_without_trades")
    if key == "paper_attribution" and _safe_int(metrics.get("attributed_trade_count")) <= 0:
        blockers.append("paper_attribution_without_attributed_trades")
    if key == "readiness_gate":
        if payload.get("decision") != DECISION_RESEARCH:
            blockers.append("readiness_gate_decision_not_research")
        if payload.get("paper_observation_allowed") is not False:
            blockers.append("readiness_gate_paper_observation_not_blocked")
        if payload.get("ready_for_shadow_observation") is not False:
            blockers.append("readiness_gate_shadow_observation_not_blocked")
    return blockers


def _extract_oos_survivor_count(report: Mapping[str, Any]) -> int:
    for field in ("oos_surviving_candidate_count", "survivor_count", "oos_survivor_count"):
        if field in report:
            return _safe_int(report.get(field))
    shortlist = report.get("oos_shortlist")
    if isinstance(shortlist, Sequence) and not isinstance(shortlist, (str, bytes)):
        return len(shortlist)
    return 0


def _readiness_snapshot(report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "present": bool(report),
        "status": report.get("status") if report else None,
        "reason": report.get("reason") if report else None,
        "decision": report.get("decision") if report else None,
        "readiness_score": report.get("readiness_score") if report else None,
        "readiness_level": report.get("readiness_level") if report else None,
        "readiness_blockers": report.get("readiness_blockers", []) if report else [],
        "paper_observation_allowed": report.get("paper_observation_allowed") if report else None,
        "ready_for_shadow_observation": report.get("ready_for_shadow_observation") if report else None,
    }


def _closeout_status(
    *,
    evidence_missing: Sequence[str],
    safety_blockers: Sequence[str],
    evidence_blockers: Sequence[str],
) -> str:
    if evidence_missing or safety_blockers:
        return "blocked"
    if evidence_blockers:
        return "warning"
    return "research_closed_blocked"


def _recommended_next_action(evidence_missing: Sequence[str], blockers: Sequence[str]) -> str:
    if evidence_missing:
        return "materializar_evidencias_explicitas_e_reexecutar_closeout_sem_liberar_observacao"
    if blockers:
        return "manter_ciclo_encerrado_bloqueado_e_corrigir_blockers_apenas_em_research"
    return "manter_ciclo_encerrado_bloqueado_e_preparar_handover_para_revisao_humana"


def build_ocr_shadow_research_evidence_closeout_report(
    *,
    project_root: str | Path,
    allow_runtime_read: bool = False,
    oos_validation_report: str | Path | None = None,
    shadow_observation_design_report: str | Path | None = None,
    shadow_observation_replay_report: str | Path | None = None,
    paper_closed_trades_attribution_report: str | Path | None = None,
    readiness_gate_report: str | Path | None = None,
    report_payloads: Mapping[str, Mapping[str, Any]] | None = None,
    write: bool = False,
    no_write: bool = True,
    output_report: str | Path | None = None,
    markdown_report: str | Path | None = None,
) -> dict[str, Any]:
    """Build the research-only closeout report."""

    root = Path(project_root).resolve()
    loaded = load_closeout_inputs(
        project_root=root,
        allow_runtime_read=allow_runtime_read,
        oos_validation_report=oos_validation_report,
        shadow_observation_design_report=shadow_observation_design_report,
        shadow_observation_replay_report=shadow_observation_replay_report,
        paper_closed_trades_attribution_report=paper_closed_trades_attribution_report,
        readiness_gate_report=readiness_gate_report,
        report_payloads=report_payloads,
    )
    closeout = compute_closeout(loaded.reports)
    write_requested = bool(write and not no_write)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "project_name": PROJECT_NAME,
        "generated_at_utc": _utc_now_iso(),
        "project_root": str(root),
        "status": "blocked",
        "reason": _reason(loaded, closeout),
        "decision": DECISION_RESEARCH,
        "closeout_decision": DECISION_RESEARCH,
        "input_mode": loaded.input_mode,
        "source_status": loaded.source_status,
        "source_reason": loaded.source_reason,
        "allow_runtime_read": allow_runtime_read,
        "write_requested": write_requested,
        "write_performed": False,
        "output_path": None,
        "markdown_output_path": None,
        "source_paths": loaded.source_paths,
        "source_sha256": loaded.source_sha256,
        "closeout_status": closeout["closeout_status"],
        "cycle_name": closeout["cycle_name"],
        "evidence_sources_required": closeout["evidence_sources_required"],
        "evidence_sources_present": closeout["evidence_sources_present"],
        "evidence_sources_missing": closeout["evidence_sources_missing"],
        "evidence_summary": closeout["evidence_summary"],
        "blocker_summary": closeout["blocker_summary"],
        "readiness_snapshot": closeout["readiness_snapshot"],
        "recommended_next_action": closeout["recommended_next_action"],
        "forbidden_next_actions": closeout["forbidden_next_actions"],
        "gate_summary": closeout["gate_summary"],
        "safety_flags": dict(SAFETY_FLAGS),
        "closeout_semantics": {
            "closeout_status": "blocked, warning or research_closed_blocked; never operational approval",
            "closeout_decision": "always MANTER_EM_RESEARCH in this branch",
            "operational_use": "forbidden: not a readiness release, not a paper observer trigger, not a runtime gate",
        },
        "required_fields": list(REQUIRED_TOP_LEVEL_FIELDS),
        "validation_errors": [],
        **SAFETY_FLAGS,
    }
    report["validation_errors"] = validate_closeout_report(report)

    if write_requested:
        output_path = _resolve_output_report(root, output_report, DEFAULT_OUTPUT_REPORT)
        markdown_path = _resolve_output_report(root, markdown_report, DEFAULT_MARKDOWN_REPORT)
        output_error = _validate_output_path(root, output_path, suffix=".json")
        markdown_error = _validate_output_path(root, markdown_path, suffix=".md")
        if output_error is not None or markdown_error is not None:
            report["reason"] = output_error or markdown_error
            report["validation_errors"] = validate_closeout_report(report)
            return report
        output_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        markdown_path.write_text(render_markdown_closeout(report), encoding="utf-8")
        report["write_performed"] = True
        report["output_path"] = _project_relative(output_path, root)
        report["markdown_output_path"] = _project_relative(markdown_path, root)
    return report


def _reason(loaded: LoadedCloseoutInputs, closeout: Mapping[str, Any]) -> str:
    if loaded.source_status != "ok":
        if loaded.input_mode == "no_runtime_rows_loaded":
            return "ocr_shadow_research_closeout_requires_explicit_runtime_read_or_in_memory_inputs"
        return loaded.source_reason
    closeout_status = closeout.get("closeout_status")
    if closeout_status == "research_closed_blocked":
        return "ocr_shadow_research_cycle_closed_research_only_operationally_blocked"
    return "ocr_shadow_research_closeout_has_blockers"


def render_markdown_closeout(report: Mapping[str, Any]) -> str:
    """Render a compact technical handover in Markdown."""

    blockers = report.get("blocker_summary", {})
    blocker_count = blockers.get("blocker_count") if isinstance(blockers, Mapping) else None
    return "\n".join(
        [
            "# OCR Shadow Research Evidence Closeout V1",
            "",
            f"- Decision: `{report.get('decision')}`",
            f"- Closeout status: `{report.get('closeout_status')}`",
            f"- Evidence present: `{report.get('evidence_sources_present')}/{report.get('evidence_sources_required')}`",
            f"- Blocker count: `{blocker_count}`",
            f"- Paper observation allowed: `{report.get('paper_observation_allowed')}`",
            f"- Ready for shadow observation: `{report.get('ready_for_shadow_observation')}`",
            f"- Recommended next action: `{report.get('recommended_next_action')}`",
            "",
            "## Operational Boundary",
            "",
            "This closeout is research-only. It does not authorize paper observer activation, rule promotion, runtime integration, orders or private exchange access.",
            "",
        ]
    )


def validate_closeout_report(report: Mapping[str, Any]) -> list[str]:
    """Validate the closeout non-operational contract."""

    errors: list[str] = []
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version_mismatch")
    if report.get("status") != "blocked":
        errors.append("status_must_remain_blocked")
    if report.get("decision") != DECISION_RESEARCH:
        errors.append("decision_must_remain_research")
    if report.get("closeout_decision") != DECISION_RESEARCH:
        errors.append("closeout_decision_must_remain_research")
    for key, expected in SAFETY_FLAGS.items():
        if report.get(key) is not expected:
            errors.append(f"{key}_must_be_{str(expected).lower()}")
        safety_flags = report.get("safety_flags")
        if not isinstance(safety_flags, Mapping) or safety_flags.get(key) is not expected:
            errors.append(f"safety_flags.{key}_must_be_{str(expected).lower()}")
    for field in REQUIRED_TOP_LEVEL_FIELDS:
        if field not in report:
            errors.append(f"missing_required_field:{field}")
    return sorted(set(errors))


def _resolve_output_report(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    if path.is_absolute():
        return path.resolve()
    return (root / path).resolve()


def _validate_output_path(root: Path, path: Path, *, suffix: str) -> str | None:
    reports_dir = (root / "data" / "reports").resolve()
    try:
        path.relative_to(reports_dir)
    except ValueError:
        return "write_blocked_output_must_be_under_data_reports"
    if path.suffix.lower() != suffix:
        return f"write_blocked_output_must_be_{suffix.removeprefix('.')}_report"
    return None
