"""Research-only readiness gate for paper shadow observation evidence.

The gate consolidates prior research reports and deliberately keeps the final
decision blocked. It does not authorize paper observation, apply rules, register
survivors, change runtime state, update models or emit signals.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence


SCHEMA_VERSION = "paper_shadow_observation_readiness_gate_v1"
PROJECT_NAME = "SMART FUTUROS"
DECISION_RESEARCH = "MANTER_EM_RESEARCH"
DEFAULT_OUTPUT_REPORT = Path("data/reports/paper_shadow_observation_readiness_gate_v1.json")

EvidenceKey = Literal["oos_validation", "observation_design", "observation_replay", "paper_attribution"]

EVIDENCE_LABELS: dict[EvidenceKey, str] = {
    "oos_validation": "OCR Master Candle Positive Rule OOS Validation",
    "observation_design": "OCR Master Candle Shadow Observation Design",
    "observation_replay": "OCR Master Candle Shadow Observation Replay",
    "paper_attribution": "Paper Closed Trades Shadow Rule Attribution",
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
    "source_status",
    "oos_survivor_count",
    "design_contract_status",
    "replay_trade_count",
    "attribution_trade_count",
    "readiness_score",
    "readiness_level",
    "readiness_blockers",
    "readiness_warnings",
    "paper_observation_allowed",
    "ready_for_shadow_observation",
    "can_apply_to_freqtrade",
    "can_apply_to_risk_manager",
    "can_promote_rules",
    "can_promote_model",
    "evidence_matrix",
    "gate_summary",
    "safety_flags",
    "write_performed",
]

FORBIDDEN_ACTIONS = [
    "liberar observacao paper-shadow",
    "ativar regra",
    "acoplar ao paper runtime",
    "registrar shadow rule",
    "aplicar shadow rule",
    "alterar Freqtrade",
    "alterar RiskManager",
    "alterar Qlib runtime",
    "alterar IA Shadow runtime",
    "alterar modelos",
    "alterar registry",
    "alterar sinais ativos",
    "enviar ordem",
    "acessar exchange privada",
]


@dataclass(frozen=True)
class LoadedReadinessInputs:
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


def load_readiness_inputs(
    *,
    project_root: str | Path,
    allow_runtime_read: bool = False,
    oos_validation_report: str | Path | None = None,
    shadow_observation_design_report: str | Path | None = None,
    shadow_observation_replay_report: str | Path | None = None,
    paper_closed_trades_attribution_report: str | Path | None = None,
    report_payloads: Mapping[str, Mapping[str, Any]] | None = None,
) -> LoadedReadinessInputs:
    """Load readiness inputs only from memory or explicit local reports."""

    root = Path(project_root).resolve()
    if report_payloads is not None:
        reports: dict[EvidenceKey, dict[str, Any]] = {}
        for key in EVIDENCE_LABELS:
            payload = report_payloads.get(key)
            if isinstance(payload, Mapping):
                reports[key] = dict(payload)
        return LoadedReadinessInputs(
            reports=reports,
            input_mode="in_memory_readiness_inputs",
            source_status="ok" if len(reports) == len(EVIDENCE_LABELS) else "blocked",
            source_reason="in_memory_inputs_supplied"
            if len(reports) == len(EVIDENCE_LABELS)
            else "missing_required_sources",
            source_paths={key: None for key in EVIDENCE_LABELS},
            source_sha256={key: None for key in EVIDENCE_LABELS},
        )
    if not allow_runtime_read:
        return LoadedReadinessInputs(
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
    }
    source_paths = {
        key: _project_relative(path, root) if path is not None else None for key, path in requested.items()
    }
    if any(path is None for path in requested.values()):
        return LoadedReadinessInputs(
            reports={},
            input_mode="runtime_read_requested",
            source_status="blocked",
            source_reason="missing_required_sources",
            source_paths=source_paths,
            source_sha256={key: None for key in EVIDENCE_LABELS},
        )
    missing_paths = [path for path in requested.values() if path is not None and not path.exists()]
    if missing_paths:
        return LoadedReadinessInputs(
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
                return LoadedReadinessInputs(
                    reports={},
                    input_mode="runtime_read_requested",
                    source_status="blocked",
                    source_reason=f"invalid_report_payload:{key}",
                    source_paths=source_paths,
                    source_sha256={name: _sha256_file(source) if source is not None else None for name, source in requested.items()},
                )
            reports[key] = dict(payload)
    except (OSError, json.JSONDecodeError) as exc:
        return LoadedReadinessInputs(
            reports={},
            input_mode="runtime_read_requested",
            source_status="blocked",
            source_reason=f"source_read_failed:{type(exc).__name__}",
            source_paths=source_paths,
            source_sha256={key: _sha256_file(path) if path is not None else None for key, path in requested.items()},
        )

    return LoadedReadinessInputs(
        reports=reports,
        input_mode="runtime_read_requested",
        source_status="ok",
        source_reason="sources_loaded_read_only",
        source_paths=source_paths,
        source_sha256={key: _sha256_file(path) if path is not None else None for key, path in requested.items()},
    )


def compute_readiness_gate(reports: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Compute deterministic readiness evidence while keeping final gates blocked."""

    evidence_matrix = [_build_evidence_row(key, reports.get(key)) for key in EVIDENCE_LABELS]
    readiness_blockers: list[str] = []
    readiness_warnings: list[str] = []
    for row in evidence_matrix:
        readiness_blockers.extend(row["blockers"])
        readiness_warnings.extend(row["warnings"])

    oos_report = reports.get("oos_validation", {})
    design_report = reports.get("observation_design", {})
    replay_report = reports.get("observation_replay", {})
    attribution_report = reports.get("paper_attribution", {})

    oos_survivor_count = _extract_oos_survivor_count(oos_report)
    design_contract_status = _extract_design_contract_status(design_report)
    replay_trade_count = _extract_replay_trade_count(replay_report)
    attribution_trade_count = _extract_attribution_trade_count(attribution_report)

    if oos_survivor_count <= 0:
        readiness_blockers.append("oos_survivors_absent")
    if design_contract_status != "present":
        readiness_blockers.append("design_contract_absent")
    if replay_trade_count <= 0:
        readiness_blockers.append("replay_report_without_trades")
    if attribution_trade_count <= 0:
        readiness_blockers.append("attribution_report_without_trades")

    gate_count = sum(len(row["checks"]) for row in evidence_matrix)
    passed_count = sum(1 for row in evidence_matrix for check in row["checks"] if check["passed"])
    readiness_score = _round((passed_count / gate_count) * 100.0) if gate_count else 0.0
    readiness_level = _readiness_level(evidence_matrix, readiness_blockers)

    return {
        "oos_survivor_count": oos_survivor_count,
        "design_contract_status": design_contract_status,
        "replay_trade_count": replay_trade_count,
        "attribution_trade_count": attribution_trade_count,
        "readiness_score": readiness_score,
        "readiness_level": readiness_level,
        "readiness_blockers": sorted(set(readiness_blockers)),
        "readiness_warnings": sorted(set(readiness_warnings)),
        "evidence_matrix": evidence_matrix,
        "gate_summary": {
            "decision": DECISION_RESEARCH,
            "evidence_sources_required": len(EVIDENCE_LABELS),
            "evidence_sources_present": sum(1 for row in evidence_matrix if row["present"]),
            "gate_checks_total": gate_count,
            "gate_checks_passed": passed_count,
            "readiness_score": readiness_score,
            "readiness_level": readiness_level,
            "paper_observation_allowed": False,
            "ready_for_shadow_observation": False,
            "operational_authority": False,
            "can_apply_to_freqtrade": False,
            "can_apply_to_risk_manager": False,
            "can_promote_rules": False,
            "sends_orders": False,
            "changes_risk": False,
            "writes_runtime": False,
            "result_can_be_used_for_operations": False,
        },
    }


def _build_evidence_row(key: EvidenceKey, payload: Mapping[str, Any] | None) -> dict[str, Any]:
    present = isinstance(payload, Mapping) and bool(payload)
    blockers: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, Any]] = []
    if not present:
        blockers.append(f"{key}_missing")
        return {
            "evidence_key": key,
            "label": EVIDENCE_LABELS[key],
            "present": False,
            "schema_version": None,
            "status": "missing",
            "reason": "missing",
            "decision": None,
            "checks": [],
            "blockers": blockers,
            "warnings": warnings,
        }

    assert payload is not None
    checks.extend(
        [
            _check_field(key, payload, "decision", DECISION_RESEARCH),
            _check_field(key, payload, "operational_authority", False),
            _check_field(key, payload, "sends_orders", False),
            _check_field(key, payload, "changes_risk", False),
            _check_field(key, payload, "can_promote_rules", False),
            _check_field(key, payload, "can_apply_to_freqtrade", False),
            _check_field(key, payload, "can_apply_to_risk_manager", False),
            _check_field(key, payload, "writes_runtime", False),
        ]
    )
    for check in checks:
        if not check["passed"]:
            blockers.append(str(check["blocker"]))
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
        "checks": checks,
        "blockers": blockers,
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
        "blocker": None if passed else f"{key}_{field}_violates_readiness_contract",
    }


def _field_value(payload: Mapping[str, Any], field: str) -> object:
    if field in payload:
        return payload.get(field)
    safety_flags = payload.get("safety_flags")
    if isinstance(safety_flags, Mapping) and field in safety_flags:
        return safety_flags.get(field)
    return None


def _extract_oos_survivor_count(report: Mapping[str, Any]) -> int:
    if not report:
        return 0
    for field in ("oos_surviving_candidate_count", "survivor_count", "oos_survivor_count"):
        if field in report:
            return _safe_int(report.get(field))
    shortlist = report.get("oos_shortlist")
    if isinstance(shortlist, Sequence) and not isinstance(shortlist, (str, bytes)):
        return len(shortlist)
    return 0


def _extract_design_contract_status(report: Mapping[str, Any]) -> str:
    if not report:
        return "missing"
    if report.get("observation_contract_version") or report.get("observation_fields"):
        return "present"
    if _safe_int(report.get("observation_record_count")) > 0:
        return "present"
    return "missing"


def _extract_replay_trade_count(report: Mapping[str, Any]) -> int:
    if not report:
        return 0
    if "replay_trade_count" in report:
        return _safe_int(report.get("replay_trade_count"))
    metrics = report.get("replay_metrics")
    if isinstance(metrics, Mapping):
        return _safe_int(metrics.get("replay_trade_count"))
    return 0


def _extract_attribution_trade_count(report: Mapping[str, Any]) -> int:
    if not report:
        return 0
    for field in ("attributed_trade_count", "attribution_trade_count"):
        if field in report:
            return _safe_int(report.get(field))
    return 0


def _readiness_level(evidence_matrix: Sequence[Mapping[str, Any]], blockers: Sequence[str]) -> str:
    if not evidence_matrix or all(not row.get("present") for row in evidence_matrix):
        return "BLOCKED"
    if any(not row.get("present") for row in evidence_matrix):
        return "INCOMPLETE"
    if blockers:
        safety_blockers = [
            blocker
            for blocker in blockers
            if blocker.endswith("_violates_readiness_contract")
            or blocker in {"oos_survivors_absent", "design_contract_absent", "replay_report_without_trades", "attribution_report_without_trades"}
        ]
        return "BLOCKED" if safety_blockers else "DIAGNOSTIC_ONLY"
    return "RESEARCH_READY_BLOCKED"


def build_paper_shadow_observation_readiness_gate_report(
    *,
    project_root: str | Path,
    allow_runtime_read: bool = False,
    oos_validation_report: str | Path | None = None,
    shadow_observation_design_report: str | Path | None = None,
    shadow_observation_replay_report: str | Path | None = None,
    paper_closed_trades_attribution_report: str | Path | None = None,
    report_payloads: Mapping[str, Mapping[str, Any]] | None = None,
    write: bool = False,
    no_write: bool = True,
    output_report: str | Path | None = None,
) -> dict[str, Any]:
    """Build the research-only readiness gate report."""

    root = Path(project_root).resolve()
    loaded = load_readiness_inputs(
        project_root=root,
        allow_runtime_read=allow_runtime_read,
        oos_validation_report=oos_validation_report,
        shadow_observation_design_report=shadow_observation_design_report,
        shadow_observation_replay_report=shadow_observation_replay_report,
        paper_closed_trades_attribution_report=paper_closed_trades_attribution_report,
        report_payloads=report_payloads,
    )
    gate = compute_readiness_gate(loaded.reports)
    write_requested = bool(write and not no_write)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "project_name": PROJECT_NAME,
        "generated_at_utc": _utc_now_iso(),
        "project_root": str(root),
        "status": "blocked",
        "reason": _reason(loaded, gate),
        "decision": DECISION_RESEARCH,
        "input_mode": loaded.input_mode,
        "source_status": loaded.source_status,
        "source_reason": loaded.source_reason,
        "allow_runtime_read": allow_runtime_read,
        "write_requested": write_requested,
        "write_performed": False,
        "output_path": None,
        "source_paths": loaded.source_paths,
        "source_sha256": loaded.source_sha256,
        "oos_survivor_count": gate["oos_survivor_count"],
        "design_contract_status": gate["design_contract_status"],
        "replay_trade_count": gate["replay_trade_count"],
        "attribution_trade_count": gate["attribution_trade_count"],
        "readiness_score": gate["readiness_score"],
        "readiness_level": gate["readiness_level"],
        "readiness_blockers": gate["readiness_blockers"],
        "readiness_warnings": gate["readiness_warnings"],
        "evidence_matrix": gate["evidence_matrix"],
        "gate_summary": gate["gate_summary"],
        "safety_flags": dict(SAFETY_FLAGS),
        "readiness_semantics": {
            "readiness_score": "descriptive research-only evidence completeness score",
            "readiness_level": "diagnostic level only; never authorizes paper observation",
            "operational_use": "forbidden: not a signal, not a runtime gate, not a rule promotion",
        },
        "required_fields": list(REQUIRED_TOP_LEVEL_FIELDS),
        "forbidden_actions": list(FORBIDDEN_ACTIONS),
        "validation_errors": [],
        **SAFETY_FLAGS,
    }
    report["validation_errors"] = validate_readiness_report(report)

    if write_requested:
        output_path = _resolve_output_report(root, output_report)
        output_error = _validate_output_report_path(root, output_path)
        if output_error is not None:
            report["reason"] = output_error
            report["validation_errors"] = validate_readiness_report(report)
            return report
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        report["write_performed"] = True
        report["output_path"] = _project_relative(output_path, root)
    return report


def _reason(loaded: LoadedReadinessInputs, gate: Mapping[str, Any]) -> str:
    if loaded.source_status != "ok":
        if loaded.input_mode == "no_runtime_rows_loaded":
            return "paper_shadow_readiness_gate_requires_explicit_runtime_read_or_in_memory_inputs"
        return loaded.source_reason
    blockers = gate.get("readiness_blockers")
    if isinstance(blockers, Sequence) and not isinstance(blockers, (str, bytes)) and blockers:
        return "paper_shadow_readiness_gate_blocked_by_research_evidence_contract"
    return "paper_shadow_readiness_gate_research_ready_but_operationally_blocked"


def validate_readiness_report(report: Mapping[str, Any]) -> list[str]:
    """Validate the readiness gate non-operational contract."""

    errors: list[str] = []
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version_mismatch")
    if report.get("status") != "blocked":
        errors.append("status_must_remain_blocked")
    if report.get("decision") != DECISION_RESEARCH:
        errors.append("decision_must_remain_research")
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


def _resolve_output_report(root: Path, output_report: str | Path | None) -> Path:
    path = Path(output_report) if output_report is not None else DEFAULT_OUTPUT_REPORT
    if path.is_absolute():
        return path.resolve()
    return (root / path).resolve()


def _validate_output_report_path(root: Path, path: Path) -> str | None:
    reports_dir = (root / "data" / "reports").resolve()
    try:
        path.relative_to(reports_dir)
    except ValueError:
        return "write_blocked_output_must_be_under_data_reports"
    if path.suffix.lower() != ".json":
        return "write_blocked_output_must_be_json_report"
    return None
