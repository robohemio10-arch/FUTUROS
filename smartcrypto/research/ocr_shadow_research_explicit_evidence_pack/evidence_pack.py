"""Research-only explicit evidence pack for the OCR Shadow Research cycle."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


SCHEMA_VERSION = "ocr_shadow_research_explicit_evidence_pack_v1"
PROJECT_NAME = "SMART FUTUROS"
DECISION_RESEARCH = "MANTER_EM_RESEARCH"
DEFAULT_OUTPUT_REPORT = Path("data/reports/ocr_shadow_research_explicit_evidence_pack_v1.json")
DEFAULT_MARKDOWN_REPORT = Path("data/reports/ocr_shadow_research_explicit_evidence_pack_v1.md")
DEFAULT_STAGE_TIMEOUT_SECONDS = 300

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


@dataclass(frozen=True)
class StageSpec:
    stage_id: str
    label: str
    script_path: str
    output_path: str
    base_args: tuple[str, ...] = ()


STAGE_SPECS: tuple[StageSpec, ...] = (
    StageSpec(
        stage_id="oos_validation",
        label="OCR Master Candle Positive Rule OOS Validation",
        script_path="scripts/build_ocr_master_candle_positive_rule_oos_validation_v1.py",
        output_path="data/reports/ocr_master_candle_positive_rule_oos_validation_v1.json",
    ),
    StageSpec(
        stage_id="observation_design",
        label="OCR Master Candle Shadow Observation Design",
        script_path="scripts/build_ocr_master_candle_shadow_observation_design_v1.py",
        output_path="data/reports/ocr_master_candle_shadow_observation_design_v1.json",
        base_args=("--oos-report", "data/reports/ocr_master_candle_positive_rule_oos_validation_v1.json"),
    ),
    StageSpec(
        stage_id="observation_replay",
        label="OCR Master Candle Shadow Observation Replay",
        script_path="scripts/build_ocr_master_candle_shadow_observation_replay_v1.py",
        output_path="data/reports/ocr_master_candle_shadow_observation_replay_v1.json",
        base_args=(
            "--observation-design-report",
            "data/reports/ocr_master_candle_shadow_observation_design_v1.json",
            "--oos-report",
            "data/reports/ocr_master_candle_positive_rule_oos_validation_v1.json",
        ),
    ),
    StageSpec(
        stage_id="paper_attribution",
        label="Paper Closed Trades Shadow Rule Attribution",
        script_path="scripts/build_paper_closed_trades_shadow_rule_attribution_v1.py",
        output_path="data/reports/paper_closed_trades_shadow_rule_attribution_v1.json",
        base_args=("--shadow-replay-report", "data/reports/ocr_master_candle_shadow_observation_replay_v1.json"),
    ),
    StageSpec(
        stage_id="readiness_gate",
        label="Paper Shadow Observation Readiness Gate",
        script_path="scripts/build_paper_shadow_observation_readiness_gate_v1.py",
        output_path="data/reports/paper_shadow_observation_readiness_gate_v1.json",
        base_args=(
            "--oos-validation-report",
            "data/reports/ocr_master_candle_positive_rule_oos_validation_v1.json",
            "--shadow-observation-design-report",
            "data/reports/ocr_master_candle_shadow_observation_design_v1.json",
            "--shadow-observation-replay-report",
            "data/reports/ocr_master_candle_shadow_observation_replay_v1.json",
            "--paper-closed-trades-attribution-report",
            "data/reports/paper_closed_trades_shadow_rule_attribution_v1.json",
        ),
    ),
    StageSpec(
        stage_id="closeout",
        label="OCR Shadow Research Evidence Closeout",
        script_path="scripts/build_ocr_shadow_research_evidence_closeout_v1.py",
        output_path="data/reports/ocr_shadow_research_evidence_closeout_v1.json",
        base_args=(
            "--oos-validation-report",
            "data/reports/ocr_master_candle_positive_rule_oos_validation_v1.json",
            "--shadow-observation-design-report",
            "data/reports/ocr_master_candle_shadow_observation_design_v1.json",
            "--shadow-observation-replay-report",
            "data/reports/ocr_master_candle_shadow_observation_replay_v1.json",
            "--paper-closed-trades-attribution-report",
            "data/reports/paper_closed_trades_shadow_rule_attribution_v1.json",
            "--readiness-gate-report",
            "data/reports/paper_shadow_observation_readiness_gate_v1.json",
        ),
    ),
)

STAGE_BY_ID = {stage.stage_id: stage for stage in STAGE_SPECS}
ALLOWED_STAGE_IDS = tuple(stage.stage_id for stage in STAGE_SPECS)

Runner = Callable[..., subprocess.CompletedProcess[str]]


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
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(numeric) or math.isinf(numeric):
        return default
    return numeric


def _safe_int(value: object) -> int:
    return int(_safe_float(value, default=0.0))


def validate_stage_selection(selected_stage_ids: Sequence[str] | None) -> tuple[list[StageSpec], list[str]]:
    """Return selected allowlisted stages and unknown stage ids."""

    if not selected_stage_ids:
        return list(STAGE_SPECS), []
    unknown = [stage_id for stage_id in selected_stage_ids if stage_id not in STAGE_BY_ID]
    if unknown:
        return [], unknown
    seen: set[str] = set()
    selected: list[StageSpec] = []
    for stage_id in selected_stage_ids:
        if stage_id in seen:
            continue
        seen.add(stage_id)
        selected.append(STAGE_BY_ID[stage_id])
    return selected, []


def _stage_command(stage: StageSpec, *, project_root: Path, write_requested: bool) -> list[str]:
    command = [
        sys.executable,
        stage.script_path,
        "--project-root",
        str(project_root),
        "--allow-runtime-read",
        *stage.base_args,
    ]
    command.append("--write" if write_requested else "--no-write")
    command.append("--json")
    return command


def run_stage(
    stage: StageSpec,
    *,
    project_root: str | Path,
    write_requested: bool,
    timeout_seconds: int = DEFAULT_STAGE_TIMEOUT_SECONDS,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    """Run one allowlisted evidence builder with shell=False and timeout."""

    root = Path(project_root).resolve()
    output_path = (root / stage.output_path).resolve()
    command = _stage_command(stage, project_root=root, write_requested=write_requested)
    try:
        completed = runner(
            command,
            cwd=str(root),
            shell=False,
            timeout=timeout_seconds,
            capture_output=True,
            text=True,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return _stage_result(
            stage,
            status="blocked",
            reason="stage_timeout",
            command=command,
            returncode=None,
            stdout=exc.stdout,
            stderr=exc.stderr,
            output_path=output_path,
            project_root=root,
            timeout_seconds=timeout_seconds,
        )
    except OSError as exc:
        return _stage_result(
            stage,
            status="blocked",
            reason=f"stage_execution_failed:{type(exc).__name__}",
            command=command,
            returncode=None,
            stdout=None,
            stderr=str(exc),
            output_path=output_path,
            project_root=root,
            timeout_seconds=timeout_seconds,
        )

    payload = _parse_stage_stdout(completed.stdout)
    stage_status = str(payload.get("status") or ("ok" if completed.returncode == 0 else "blocked"))
    reason = str(payload.get("reason") or ("stage_completed" if completed.returncode == 0 else "stage_returncode_nonzero"))
    if completed.returncode != 0:
        stage_status = "blocked"
    return _stage_result(
        stage,
        status=stage_status,
        reason=reason,
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        output_path=output_path,
        project_root=root,
        timeout_seconds=timeout_seconds,
        payload=payload,
    )


def _stage_result(
    stage: StageSpec,
    *,
    status: str,
    reason: str,
    command: Sequence[str],
    returncode: int | None,
    stdout: str | bytes | None,
    stderr: str | bytes | None,
    output_path: Path,
    project_root: Path,
    timeout_seconds: int = DEFAULT_STAGE_TIMEOUT_SECONDS,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    output_exists = output_path.exists()
    return {
        "stage_id": stage.stage_id,
        "label": stage.label,
        "script_path": stage.script_path,
        "status": status,
        "reason": reason,
        "returncode": returncode,
        "command": list(command),
        "shell": False,
        "timeout_seconds": timeout_seconds,
        "stdout_sample": _sample_text(stdout),
        "stderr_sample": _sample_text(stderr),
        "output_path": _project_relative(output_path, project_root),
        "output_exists": output_exists,
        "sha256": _sha256_file(output_path) if output_exists else None,
        "decision": payload.get("decision") if payload else None,
        "write_performed": payload.get("write_performed") if payload else False,
        "paper_observation_allowed": payload.get("paper_observation_allowed") if payload else False,
        "ready_for_shadow_observation": payload.get("ready_for_shadow_observation") if payload else False,
        "operational_authority": payload.get("operational_authority") if payload else False,
        "sends_orders": payload.get("sends_orders") if payload else False,
        "changes_risk": payload.get("changes_risk") if payload else False,
        "writes_runtime": payload.get("writes_runtime") if payload else False,
        "safety_flags": dict(SAFETY_FLAGS),
    }


def _parse_stage_stdout(stdout: str | bytes | None) -> dict[str, Any]:
    if stdout is None:
        return {}
    text = stdout.decode("utf-8", errors="replace") if isinstance(stdout, bytes) else stdout
    stripped = text.strip()
    if not stripped:
        return {}
    try:
        payload = json.loads(stripped.splitlines()[-1])
    except json.JSONDecodeError:
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _sample_text(value: str | bytes | None, limit: int = 1000) -> str:
    if value is None:
        return ""
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    return text[:limit]


def build_ocr_shadow_research_explicit_evidence_pack_report(
    *,
    project_root: str | Path,
    allow_runtime_read: bool = False,
    execute_builders: bool = False,
    selected_stage_ids: Sequence[str] | None = None,
    write: bool = False,
    no_write: bool = True,
    output_report: str | Path | None = None,
    markdown_report: str | Path | None = None,
    timeout_seconds: int = DEFAULT_STAGE_TIMEOUT_SECONDS,
    runner: Runner = subprocess.run,
    fixture_stage_results: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the explicit evidence pack report."""

    root = Path(project_root).resolve()
    write_requested = bool(write and not no_write)
    selected_stages, unknown_stages = validate_stage_selection(selected_stage_ids)
    stage_results: list[dict[str, Any]] = []
    if fixture_stage_results is not None:
        stage_results = [dict(item) for item in fixture_stage_results]
    elif allow_runtime_read and execute_builders and not unknown_stages:
        stage_results = [
            run_stage(
                stage,
                project_root=root,
                write_requested=write_requested,
                timeout_seconds=timeout_seconds,
                runner=runner,
            )
            for stage in selected_stages
        ]

    evidence_artifacts = _collect_artifacts(root, stage_results)
    closeout_summary = _summary_for_stage(stage_results, "closeout")
    readiness_summary = _summary_for_stage(stage_results, "readiness_gate")
    failed_stage_count = sum(1 for result in stage_results if result.get("returncode") not in (0, None))
    blocked_stage_count = sum(1 for result in stage_results if result.get("status") == "blocked")
    executed_stage_count = len(stage_results)
    reason = _reason(
        allow_runtime_read=allow_runtime_read,
        execute_builders=execute_builders,
        unknown_stages=unknown_stages,
        failed_stage_count=failed_stage_count,
        blocked_stage_count=blocked_stage_count,
    )
    status = "blocked"
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "project_name": PROJECT_NAME,
        "generated_at_utc": _utc_now_iso(),
        "project_root": str(root),
        "status": status,
        "reason": reason,
        "decision": DECISION_RESEARCH,
        "evidence_pack_status": "blocked",
        "evidence_pack_decision": DECISION_RESEARCH,
        "input_mode": "runtime_builder_execution_requested"
        if allow_runtime_read and execute_builders
        else "no_runtime_rows_loaded",
        "allow_runtime_read": allow_runtime_read,
        "execute_builders": execute_builders,
        "write_requested": write_requested,
        "write_performed": False,
        "output_path": None,
        "markdown_output_path": None,
        "stage_count": len(selected_stages),
        "executed_stage_count": executed_stage_count,
        "blocked_stage_count": blocked_stage_count,
        "failed_stage_count": failed_stage_count,
        "unknown_stage_ids": unknown_stages,
        "evidence_sources_present": sum(1 for artifact in evidence_artifacts if artifact["exists"]),
        "evidence_sources_required": len(STAGE_SPECS),
        "evidence_artifacts": evidence_artifacts,
        "stage_results": stage_results,
        "closeout_summary": closeout_summary,
        "readiness_summary": readiness_summary,
        "recommended_next_action": _recommended_next_action(
            allow_runtime_read=allow_runtime_read,
            execute_builders=execute_builders,
            unknown_stages=unknown_stages,
            failed_stage_count=failed_stage_count,
        ),
        "forbidden_next_actions": list(FORBIDDEN_NEXT_ACTIONS),
        "gate_summary": _gate_summary(),
        "safety_flags": dict(SAFETY_FLAGS),
        "validation_errors": [],
        **SAFETY_FLAGS,
    }
    report["validation_errors"] = validate_evidence_pack_report(report)

    if write_requested:
        output_path = _resolve_output_report(root, output_report, DEFAULT_OUTPUT_REPORT)
        markdown_path = _resolve_output_report(root, markdown_report, DEFAULT_MARKDOWN_REPORT)
        output_error = _validate_output_path(root, output_path, suffix=".json")
        markdown_error = _validate_output_path(root, markdown_path, suffix=".md")
        if output_error is not None or markdown_error is not None:
            report["reason"] = output_error or markdown_error
            report["validation_errors"] = validate_evidence_pack_report(report)
            return report
        output_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        markdown_path.write_text(render_markdown_evidence_pack(report), encoding="utf-8")
        report["write_performed"] = True
        report["output_path"] = _project_relative(output_path, root)
        report["markdown_output_path"] = _project_relative(markdown_path, root)
    return report


def _reason(
    *,
    allow_runtime_read: bool,
    execute_builders: bool,
    unknown_stages: Sequence[str],
    failed_stage_count: int,
    blocked_stage_count: int,
) -> str:
    if unknown_stages:
        return "unknown_stage_not_in_allowlist"
    if not allow_runtime_read:
        return "explicit_evidence_pack_requires_allow_runtime_read"
    if not execute_builders:
        return "explicit_evidence_pack_requires_execute_builders"
    if failed_stage_count:
        return "explicit_evidence_pack_stage_failed"
    if blocked_stage_count:
        return "explicit_evidence_pack_contains_blocked_research_stages"
    return "explicit_evidence_pack_completed_research_only_operationally_blocked"


def _collect_artifacts(root: Path, stage_results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result_by_stage = {str(result.get("stage_id")): result for result in stage_results}
    artifacts: list[dict[str, Any]] = []
    for stage in STAGE_SPECS:
        path = (root / stage.output_path).resolve()
        result = result_by_stage.get(stage.stage_id, {})
        artifacts.append(
            {
                "stage_id": stage.stage_id,
                "label": stage.label,
                "path": stage.output_path,
                "exists": bool(result.get("output_exists")) or path.exists(),
                "sha256": result.get("sha256") or _sha256_file(path),
                "source": "stage_result" if result else "filesystem_probe",
            }
        )
    return artifacts


def _summary_for_stage(stage_results: Sequence[Mapping[str, Any]], stage_id: str) -> dict[str, Any]:
    for result in stage_results:
        if result.get("stage_id") == stage_id:
            return {
                "present": True,
                "stage_id": stage_id,
                "status": result.get("status"),
                "reason": result.get("reason"),
                "decision": result.get("decision"),
                "write_performed": result.get("write_performed"),
                "paper_observation_allowed": result.get("paper_observation_allowed"),
                "ready_for_shadow_observation": result.get("ready_for_shadow_observation"),
                "operational_authority": result.get("operational_authority"),
            }
    return {"present": False, "stage_id": stage_id}


def _recommended_next_action(
    *,
    allow_runtime_read: bool,
    execute_builders: bool,
    unknown_stages: Sequence[str],
    failed_stage_count: int,
) -> str:
    if unknown_stages:
        return "corrigir_stage_selection_para_allowlist_fixa_sem_executar_scripts_arbitrarios"
    if not allow_runtime_read or not execute_builders:
        return "executar_evidence_pack_com_allow_runtime_read_execute_builders_e_write_apenas_para_materializar_relatorios"
    if failed_stage_count:
        return "inspecionar_stage_results_e_corrigir_falhas_apenas_em_research_sem_liberar_observacao"
    return "revisar_evidence_pack_consolidado_e_manter_decisao_em_research"


def _gate_summary() -> dict[str, Any]:
    return {
        "decision": DECISION_RESEARCH,
        "evidence_pack_decision": DECISION_RESEARCH,
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


def validate_evidence_pack_report(report: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version_mismatch")
    if report.get("status") != "blocked":
        errors.append("status_must_remain_blocked")
    if report.get("decision") != DECISION_RESEARCH:
        errors.append("decision_must_remain_research")
    if report.get("evidence_pack_decision") != DECISION_RESEARCH:
        errors.append("evidence_pack_decision_must_remain_research")
    for key, expected in SAFETY_FLAGS.items():
        if report.get(key) is not expected:
            errors.append(f"{key}_must_be_{str(expected).lower()}")
        safety_flags = report.get("safety_flags")
        if not isinstance(safety_flags, Mapping) or safety_flags.get(key) is not expected:
            errors.append(f"safety_flags.{key}_must_be_{str(expected).lower()}")
    return sorted(set(errors))


def render_markdown_evidence_pack(report: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# OCR Shadow Research Explicit Evidence Pack V1",
            "",
            f"- Decision: `{report.get('decision')}`",
            f"- Evidence pack decision: `{report.get('evidence_pack_decision')}`",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Executed stages: `{report.get('executed_stage_count')}/{report.get('stage_count')}`",
            f"- Evidence present: `{report.get('evidence_sources_present')}/{report.get('evidence_sources_required')}`",
            f"- Paper observation allowed: `{report.get('paper_observation_allowed')}`",
            f"- Ready for shadow observation: `{report.get('ready_for_shadow_observation')}`",
            f"- Recommended next action: `{report.get('recommended_next_action')}`",
            "",
            "## Operational Boundary",
            "",
            "This pack is research-only. It does not authorize paper observer activation, rule promotion, runtime integration, orders or private exchange access.",
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
