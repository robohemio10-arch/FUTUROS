"""Research-only diagnostics for OCR shadow replay and paper attribution sources."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence


SCHEMA_VERSION = "ocr_shadow_paper_replay_attribution_source_diagnostics_v1"
PROJECT_NAME = "SMART FUTUROS"
DECISION_RESEARCH = "MANTER_EM_RESEARCH"
DEFAULT_OUTPUT_REPORT = Path("data/reports/ocr_shadow_paper_replay_attribution_source_diagnostics_v1.json")
DEFAULT_MARKDOWN_REPORT = Path("data/reports/ocr_shadow_paper_replay_attribution_source_diagnostics_v1.md")

ReportKey = Literal[
    "oos_validation",
    "observation_design",
    "observation_replay",
    "paper_attribution",
    "readiness_gate",
    "closeout",
    "evidence_pack",
]

EXPECTED_REPORTS: dict[ReportKey, tuple[str, str]] = {
    "oos_validation": (
        "OCR Master Candle Positive Rule OOS Validation",
        "data/reports/ocr_master_candle_positive_rule_oos_validation_v1.json",
    ),
    "observation_design": (
        "OCR Master Candle Shadow Observation Design",
        "data/reports/ocr_master_candle_shadow_observation_design_v1.json",
    ),
    "observation_replay": (
        "OCR Master Candle Shadow Observation Replay",
        "data/reports/ocr_master_candle_shadow_observation_replay_v1.json",
    ),
    "paper_attribution": (
        "Paper Closed Trades Shadow Rule Attribution",
        "data/reports/paper_closed_trades_shadow_rule_attribution_v1.json",
    ),
    "readiness_gate": (
        "Paper Shadow Observation Readiness Gate",
        "data/reports/paper_shadow_observation_readiness_gate_v1.json",
    ),
    "closeout": (
        "OCR Shadow Research Evidence Closeout",
        "data/reports/ocr_shadow_research_evidence_closeout_v1.json",
    ),
    "evidence_pack": (
        "OCR Shadow Research Explicit Evidence Pack",
        "data/reports/ocr_shadow_research_explicit_evidence_pack_v1.json",
    ),
}

JOIN_FIELDS = (
    "trade_id",
    "order_id",
    "internal_order_id",
    "fingerprint_operacional",
    "symbol",
    "side",
    "open_time",
    "close_time",
)
PNL_FIELDS = ("pnl", "profit_abs", "net_pnl", "pnl_fechado", "reported_pnl_usdt", "raw_pnl_usdt")
SURVIVOR_FIELDS = ("survivor_rule_id", "matched_survivor_rule_id", "candidate_id")

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
    "promover survivor",
    "aplicar regra",
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
    "evidence_sources_checked",
    "evidence_sources_present",
    "evidence_sources_missing",
    "report_sha256",
    "replay_diagnostics",
    "attribution_diagnostics",
    "contract_diagnostics",
    "root_cause_candidates",
    "missing_sources",
    "missing_fields",
    "contract_mismatches",
    "recommended_next_action",
    "forbidden_next_actions",
    "gate_summary",
    "safety_flags",
    "write_performed",
]


@dataclass(frozen=True)
class LoadedDiagnosticInputs:
    reports: dict[ReportKey, dict[str, Any]]
    input_mode: str
    source_status: str
    source_reason: str
    source_paths: dict[str, str | None]
    report_sha256: dict[str, str | None]
    missing_sources: list[str]


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


def _resolve_path(root: Path, value: str | Path | None) -> Path | None:
    if value is None:
        return None
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    return (root / path).resolve()


def load_diagnostic_inputs(
    *,
    project_root: str | Path,
    allow_runtime_read: bool = False,
    report_paths: Mapping[str, str | Path] | None = None,
    report_payloads: Mapping[str, Mapping[str, Any]] | None = None,
) -> LoadedDiagnosticInputs:
    """Load diagnostic reports from memory or explicit local report files."""

    root = Path(project_root).resolve()
    if report_payloads is not None:
        reports: dict[ReportKey, dict[str, Any]] = {}
        for key in EXPECTED_REPORTS:
            payload = report_payloads.get(key)
            if isinstance(payload, Mapping):
                reports[key] = dict(payload)
        missing = [key for key in EXPECTED_REPORTS if key not in reports]
        return LoadedDiagnosticInputs(
            reports=reports,
            input_mode="in_memory_diagnostic_inputs",
            source_status="ok" if not missing else "blocked",
            source_reason="in_memory_inputs_supplied" if not missing else "missing_required_sources",
            source_paths={key: None for key in EXPECTED_REPORTS},
            report_sha256={key: None for key in EXPECTED_REPORTS},
            missing_sources=missing,
        )
    if not allow_runtime_read:
        return LoadedDiagnosticInputs(
            reports={},
            input_mode="no_runtime_rows_loaded",
            source_status="blocked",
            source_reason="runtime_read_not_allowed_by_default",
            source_paths={key: None for key in EXPECTED_REPORTS},
            report_sha256={key: None for key in EXPECTED_REPORTS},
            missing_sources=list(EXPECTED_REPORTS),
        )

    resolved: dict[ReportKey, Path] = {}
    source_paths: dict[str, str | None] = {}
    for key, (_, default_path) in EXPECTED_REPORTS.items():
        raw_path = report_paths.get(key) if report_paths else None
        path = _resolve_path(root, raw_path or default_path)
        assert path is not None
        resolved[key] = path
        source_paths[key] = _project_relative(path, root)

    reports: dict[ReportKey, dict[str, Any]] = {}
    missing_sources: list[str] = []
    sha256: dict[str, str | None] = {}
    for key, path in resolved.items():
        sha256[key] = _sha256_file(path)
        if not path.exists():
            missing_sources.append(key)
            continue
        try:
            payload = _read_json(path)
        except (OSError, json.JSONDecodeError):
            missing_sources.append(f"{key}:unreadable_json")
            continue
        if not isinstance(payload, Mapping):
            missing_sources.append(f"{key}:invalid_payload")
            continue
        reports[key] = dict(payload)

    return LoadedDiagnosticInputs(
        reports=reports,
        input_mode="runtime_read_requested",
        source_status="ok" if not missing_sources else "blocked",
        source_reason="sources_loaded_read_only" if not missing_sources else "missing_or_invalid_sources",
        source_paths=source_paths,
        report_sha256=sha256,
        missing_sources=missing_sources,
    )


def compute_source_diagnostics(reports: Mapping[str, Mapping[str, Any]], missing_sources: Sequence[str]) -> dict[str, Any]:
    """Compute replay/attribution source diagnostics from loaded reports."""

    replay_report = reports.get("observation_replay", {})
    attribution_report = reports.get("paper_attribution", {})
    oos_report = reports.get("oos_validation", {})
    design_report = reports.get("observation_design", {})
    readiness_report = reports.get("readiness_gate", {})
    closeout_report = reports.get("closeout", {})
    pack_report = reports.get("evidence_pack", {})

    replay_diagnostics = _replay_diagnostics(replay_report)
    attribution_diagnostics = _attribution_diagnostics(attribution_report)
    contract_diagnostics = _contract_diagnostics(
        oos_report=oos_report,
        design_report=design_report,
        replay_report=replay_report,
        attribution_report=attribution_report,
        readiness_report=readiness_report,
        closeout_report=closeout_report,
        pack_report=pack_report,
    )
    missing_fields = _missing_fields(replay_report, attribution_report)
    root_causes = _root_cause_candidates(
        missing_sources=missing_sources,
        replay_diagnostics=replay_diagnostics,
        attribution_diagnostics=attribution_diagnostics,
        contract_diagnostics=contract_diagnostics,
        missing_fields=missing_fields,
    )
    return {
        "replay_diagnostics": replay_diagnostics,
        "attribution_diagnostics": attribution_diagnostics,
        "contract_diagnostics": contract_diagnostics,
        "root_cause_candidates": root_causes,
        "missing_fields": missing_fields,
        "contract_mismatches": contract_diagnostics["contract_mismatches"],
        "recommended_next_action": _recommended_next_action(root_causes),
    }


def _replay_diagnostics(report: Mapping[str, Any]) -> dict[str, Any]:
    metrics = report.get("replay_metrics")
    if not isinstance(metrics, Mapping):
        metrics = {}
    survivor_count = _safe_int(report.get("survivor_count") or report.get("survivor_record_count"))
    closed_trade_count = _safe_int(report.get("closed_trade_count") or metrics.get("replay_trade_count"))
    replay_trade_count = _safe_int(metrics.get("replay_trade_count") or report.get("replay_trade_count"))
    would_allow_count = _safe_int(metrics.get("would_allow_count") or report.get("would_allow_count"))
    would_block_count = _safe_int(metrics.get("would_block_count") or report.get("would_block_count"))
    trades_source_path = report.get("trades_source_path") or report.get("closed_trades_source_path")
    survivor_source_path = report.get("survivor_source_path") or report.get("observation_design_report")
    blockers: list[str] = []
    if survivor_count <= 0:
        blockers.append("replay_missing_survivors_or_observation_records")
    if not survivor_source_path:
        blockers.append("replay_missing_observation_design_or_oos_source")
    if closed_trade_count <= 0:
        blockers.append("replay_missing_closed_trades")
    if not trades_source_path:
        blockers.append("replay_missing_closed_trades_source")
    if replay_trade_count <= 0:
        blockers.append("replay_report_without_trades")
    return {
        "status": report.get("status") if report else "missing",
        "reason": report.get("reason") if report else "missing",
        "survivor_count": survivor_count,
        "closed_trade_count": closed_trade_count,
        "replay_trade_count": replay_trade_count,
        "would_allow_count": would_allow_count,
        "would_block_count": would_block_count,
        "survivor_source_path": survivor_source_path,
        "trades_source_path": trades_source_path,
        "received_observation_design_or_oos_report": bool(survivor_source_path or survivor_count > 0),
        "received_closed_trades_source": bool(trades_source_path and closed_trade_count > 0),
        "blockers": blockers,
    }


def _attribution_diagnostics(report: Mapping[str, Any]) -> dict[str, Any]:
    closed_trade_count = _safe_int(report.get("closed_trade_count"))
    attributed_trade_count = _safe_int(report.get("attributed_trade_count"))
    unattributed_trade_count = _safe_int(report.get("unattributed_trade_count"))
    replay_row_count = _safe_int(report.get("replay_row_count"))
    survivor_record_count = _safe_int(report.get("survivor_record_count"))
    replay_source_path = report.get("shadow_replay_source_path")
    closed_source_path = report.get("closed_trades_source_path")
    blockers: list[str] = []
    if closed_trade_count <= 0:
        blockers.append("attribution_missing_closed_trades")
    if not closed_source_path:
        blockers.append("attribution_missing_closed_trades_source")
    if replay_row_count <= 0 and survivor_record_count <= 0:
        blockers.append("attribution_missing_shadow_replay_rows_or_survivors")
    if not replay_source_path:
        blockers.append("attribution_missing_shadow_replay_source")
    if attributed_trade_count <= 0:
        blockers.append("paper_attribution_without_attributed_trades")
    return {
        "status": report.get("status") if report else "missing",
        "reason": report.get("reason") if report else "missing",
        "closed_trade_count": closed_trade_count,
        "attributed_trade_count": attributed_trade_count,
        "unattributed_trade_count": unattributed_trade_count,
        "replay_row_count": replay_row_count,
        "survivor_record_count": survivor_record_count,
        "shadow_replay_source_path": replay_source_path,
        "closed_trades_source_path": closed_source_path,
        "received_shadow_replay_report": bool(replay_source_path and (replay_row_count > 0 or survivor_record_count > 0)),
        "received_closed_trades_source": bool(closed_source_path and closed_trade_count > 0),
        "blockers": blockers,
    }


def _contract_diagnostics(
    *,
    oos_report: Mapping[str, Any],
    design_report: Mapping[str, Any],
    replay_report: Mapping[str, Any],
    attribution_report: Mapping[str, Any],
    readiness_report: Mapping[str, Any],
    closeout_report: Mapping[str, Any],
    pack_report: Mapping[str, Any],
) -> dict[str, Any]:
    oos_survivor_count = _extract_oos_survivor_count(oos_report)
    observation_record_count = _safe_int(design_report.get("observation_record_count") or design_report.get("survivor_count"))
    replay_metrics = replay_report.get("replay_metrics") if isinstance(replay_report.get("replay_metrics"), Mapping) else {}
    replay_trade_count = _safe_int(replay_metrics.get("replay_trade_count") if isinstance(replay_metrics, Mapping) else 0)
    attributed_trade_count = _safe_int(attribution_report.get("attributed_trade_count"))
    mismatches: list[str] = []
    if oos_survivor_count > 0 and observation_record_count <= 0:
        mismatches.append("oos_survivors_not_materialized_as_observation_records")
    if observation_record_count > 0 and replay_trade_count <= 0:
        mismatches.append("observation_records_not_replayed_against_closed_trades")
    if replay_trade_count > 0 and attributed_trade_count <= 0:
        mismatches.append("replay_trades_not_attributed_to_paper_closed_trades")
    readiness_blockers = readiness_report.get("readiness_blockers", [])
    if isinstance(readiness_blockers, Sequence) and not isinstance(readiness_blockers, (str, bytes)):
        for blocker in readiness_blockers:
            if str(blocker) in {"replay_report_without_trades", "attribution_report_without_trades"}:
                mismatches.append(f"readiness_gate:{blocker}")
    closeout_blockers = _extract_closeout_blockers(closeout_report)
    for blocker in closeout_blockers:
        if "replay_report_without_trades" in blocker or "attribution_report_without_trades" in blocker:
            mismatches.append(f"closeout:{blocker}")
    pack_stage_results = pack_report.get("stage_results", [])
    if isinstance(pack_stage_results, Sequence) and not isinstance(pack_stage_results, (str, bytes)):
        blocked_stage_ids = [str(item.get("stage_id")) for item in pack_stage_results if isinstance(item, Mapping) and item.get("status") == "blocked"]
    else:
        blocked_stage_ids = []
    return {
        "oos_survivor_count": oos_survivor_count,
        "observation_record_count": observation_record_count,
        "replay_trade_count": replay_trade_count,
        "attributed_trade_count": attributed_trade_count,
        "readiness_blockers": list(readiness_blockers) if isinstance(readiness_blockers, list) else [],
        "closeout_blockers": closeout_blockers,
        "blocked_pack_stage_ids": blocked_stage_ids,
        "contract_mismatches": sorted(set(mismatches)),
    }


def _extract_closeout_blockers(report: Mapping[str, Any]) -> list[str]:
    blocker_summary = report.get("blocker_summary")
    if isinstance(blocker_summary, Mapping):
        blockers = blocker_summary.get("blockers")
        if isinstance(blockers, list):
            return [str(item) for item in blockers]
    return []


def _extract_oos_survivor_count(report: Mapping[str, Any]) -> int:
    for field in ("oos_surviving_candidate_count", "survivor_count", "oos_survivor_count"):
        if field in report:
            return _safe_int(report.get(field))
    shortlist = report.get("oos_shortlist")
    if isinstance(shortlist, Sequence) and not isinstance(shortlist, (str, bytes)):
        return len(shortlist)
    return 0


def _missing_fields(replay_report: Mapping[str, Any], attribution_report: Mapping[str, Any]) -> dict[str, list[str]]:
    replay_rows = _extract_rows(replay_report, ("replay_rows_sample", "replay_rows", "attribution_table_sample"))
    attribution_rows = _extract_rows(attribution_report, ("attribution_table_sample", "replay_rows", "rows"))
    return {
        "replay_rows": _missing_from_rows(replay_rows, (*JOIN_FIELDS, *PNL_FIELDS, *SURVIVOR_FIELDS)),
        "attribution_rows": _missing_from_rows(attribution_rows, (*JOIN_FIELDS, *PNL_FIELDS, *SURVIVOR_FIELDS)),
    }


def _extract_rows(report: Mapping[str, Any], keys: Sequence[str]) -> list[dict[str, Any]]:
    for key in keys:
        value = report.get(key)
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, Mapping)]
    metrics = report.get("replay_metrics")
    if isinstance(metrics, Mapping):
        for key in keys:
            value = metrics.get(key)
            if isinstance(value, list):
                return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def _missing_from_rows(rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> list[str]:
    if not rows:
        return ["no_sample_rows_available"]
    missing: list[str] = []
    for field in fields:
        if not any(field in row and row.get(field) not in (None, "") for row in rows):
            missing.append(field)
    return missing


def _root_cause_candidates(
    *,
    missing_sources: Sequence[str],
    replay_diagnostics: Mapping[str, Any],
    attribution_diagnostics: Mapping[str, Any],
    contract_diagnostics: Mapping[str, Any],
    missing_fields: Mapping[str, list[str]],
) -> list[str]:
    causes: list[str] = []
    if missing_sources:
        causes.append("expected_research_reports_missing_or_unreadable")
    if "replay_missing_closed_trades_source" in replay_diagnostics.get("blockers", []):
        causes.append("observation_replay_was_not_given_closed_trades_source")
    if "replay_report_without_trades" in replay_diagnostics.get("blockers", []):
        causes.append("observation_replay_had_zero_closed_trades_to_replay")
    if "attribution_missing_closed_trades_source" in attribution_diagnostics.get("blockers", []):
        causes.append("paper_attribution_was_not_given_closed_trades_source")
    if "attribution_missing_shadow_replay_source" in attribution_diagnostics.get("blockers", []):
        causes.append("paper_attribution_was_not_given_shadow_replay_report")
    if "paper_attribution_without_attributed_trades" in attribution_diagnostics.get("blockers", []):
        causes.append("paper_attribution_had_zero_attributed_trades")
    for mismatch in contract_diagnostics.get("contract_mismatches", []):
        causes.append(f"contract_mismatch:{mismatch}")
    if any(field in missing_fields.get("attribution_rows", []) for field in ("trade_id", "order_id", "fingerprint_operacional")):
        causes.append("attribution_rows_missing_stable_join_identifiers")
    if any(field in missing_fields.get("replay_rows", []) for field in ("trade_id", "order_id", "fingerprint_operacional")):
        causes.append("replay_rows_missing_stable_join_identifiers")
    return sorted(set(causes))


def _recommended_next_action(root_causes: Sequence[str]) -> str:
    if not root_causes:
        return "manter_diagnostico_em_research_e_revisar_evidencias_humanamente"
    if any("closed_trades_source" in cause for cause in root_causes):
        return "materializar_fonte_readonly_de_trades_fechados_paper_e_reexecutar_replay_attribution_sem_liberar_observacao"
    if any("stable_join" in cause for cause in root_causes):
        return "corrigir_contrato_de_identificadores_em_research_sem_aplicar_regras_ou_alterar_runtime"
    return "corrigir_fontes_ou_contratos_em_research_e_reexecutar_evidence_pack_sem_liberar_observacao"


def build_ocr_shadow_paper_replay_attribution_source_diagnostics_report(
    *,
    project_root: str | Path,
    allow_runtime_read: bool = False,
    report_paths: Mapping[str, str | Path] | None = None,
    report_payloads: Mapping[str, Mapping[str, Any]] | None = None,
    write: bool = False,
    no_write: bool = True,
    output_report: str | Path | None = None,
    markdown_report: str | Path | None = None,
) -> dict[str, Any]:
    """Build the research-only source diagnostics report."""

    root = Path(project_root).resolve()
    loaded = load_diagnostic_inputs(
        project_root=root,
        allow_runtime_read=allow_runtime_read,
        report_paths=report_paths,
        report_payloads=report_payloads,
    )
    diagnostics = compute_source_diagnostics(loaded.reports, loaded.missing_sources)
    write_requested = bool(write and not no_write)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "project_name": PROJECT_NAME,
        "generated_at_utc": _utc_now_iso(),
        "project_root": str(root),
        "status": "blocked",
        "reason": _reason(loaded, diagnostics),
        "decision": DECISION_RESEARCH,
        "input_mode": loaded.input_mode,
        "source_status": loaded.source_status,
        "source_reason": loaded.source_reason,
        "allow_runtime_read": allow_runtime_read,
        "write_requested": write_requested,
        "write_performed": False,
        "output_path": None,
        "markdown_output_path": None,
        "source_paths": loaded.source_paths,
        "evidence_sources_checked": list(EXPECTED_REPORTS),
        "evidence_sources_present": sorted(loaded.reports),
        "evidence_sources_missing": loaded.missing_sources,
        "report_sha256": loaded.report_sha256,
        "replay_diagnostics": diagnostics["replay_diagnostics"],
        "attribution_diagnostics": diagnostics["attribution_diagnostics"],
        "contract_diagnostics": diagnostics["contract_diagnostics"],
        "root_cause_candidates": diagnostics["root_cause_candidates"],
        "missing_sources": loaded.missing_sources,
        "missing_fields": diagnostics["missing_fields"],
        "contract_mismatches": diagnostics["contract_mismatches"],
        "recommended_next_action": diagnostics["recommended_next_action"],
        "forbidden_next_actions": list(FORBIDDEN_NEXT_ACTIONS),
        "gate_summary": _gate_summary(),
        "safety_flags": dict(SAFETY_FLAGS),
        "validation_errors": [],
        **SAFETY_FLAGS,
    }
    report["validation_errors"] = validate_diagnostics_report(report)

    if write_requested:
        output_path = _resolve_output_report(root, output_report, DEFAULT_OUTPUT_REPORT)
        markdown_path = _resolve_output_report(root, markdown_report, DEFAULT_MARKDOWN_REPORT)
        output_error = _validate_output_path(root, output_path, suffix=".json")
        markdown_error = _validate_output_path(root, markdown_path, suffix=".md")
        if output_error is not None or markdown_error is not None:
            report["reason"] = output_error or markdown_error
            report["validation_errors"] = validate_diagnostics_report(report)
            return report
        output_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        markdown_path.write_text(render_markdown_diagnostics(report), encoding="utf-8")
        report["write_performed"] = True
        report["output_path"] = _project_relative(output_path, root)
        report["markdown_output_path"] = _project_relative(markdown_path, root)
    return report


def _reason(loaded: LoadedDiagnosticInputs, diagnostics: Mapping[str, Any]) -> str:
    if loaded.source_status != "ok":
        if loaded.input_mode == "no_runtime_rows_loaded":
            return "source_diagnostics_requires_explicit_runtime_read_or_in_memory_inputs"
        return loaded.source_reason
    if diagnostics.get("root_cause_candidates"):
        return "source_diagnostics_identified_research_blockers"
    return "source_diagnostics_completed_research_only_no_operational_authority"


def _gate_summary() -> dict[str, Any]:
    return {
        "decision": DECISION_RESEARCH,
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
    }


def validate_diagnostics_report(report: Mapping[str, Any]) -> list[str]:
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


def render_markdown_diagnostics(report: Mapping[str, Any]) -> str:
    root_causes = report.get("root_cause_candidates", [])
    root_cause_lines = "\n".join(f"- `{cause}`" for cause in root_causes) if root_causes else "- none"
    return "\n".join(
        [
            "# OCR Shadow Paper Replay Attribution Source Diagnostics V1",
            "",
            f"- Decision: `{report.get('decision')}`",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Evidence present: `{len(report.get('evidence_sources_present', []))}/{len(report.get('evidence_sources_checked', []))}`",
            f"- Recommended next action: `{report.get('recommended_next_action')}`",
            "",
            "## Root Cause Candidates",
            "",
            root_cause_lines,
            "",
            "## Operational Boundary",
            "",
            "This diagnostic is research-only. It does not authorize paper observer activation, survivor promotion, runtime integration, orders or private exchange access.",
            "",
        ]
    )


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
