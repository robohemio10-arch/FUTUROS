"""Paper-only scheduler contract for the SMART FUTUROS Daily Learning Loop.

This module builds a deterministic scheduling contract and execution plan for
research review only. It does not register cron, systemd timers, Windows tasks,
containers, services, or any other scheduler. It also does not execute the Daily
Learning orchestrator, train models, promote rules, or touch runtime state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Any, Mapping


PROJECT_NAME = "SMART FUTUROS"
DAILY_LEARNING_SCHEDULER_PAPER_SCHEMA_VERSION = "daily_learning_scheduler_paper_v1"
DECISION_KEEP_RESEARCH = "MANTER_EM_RESEARCH"
STATUS_BLOCKED = "blocked"
FINAL_OPERATOR_DECISION = "BLOQUEADO_PARA_OPERACAO_MANTER_EM_RESEARCH"
ORCHESTRATOR_SCRIPT = "scripts/build_daily_paper_master_learning_loop_orchestrator_v1.py"

FORBIDDEN_OUTPUT_ROOTS = ("data", "runtime", "reports", "logs", "freqtrade")

FORBIDDEN_ACTIONS = (
    "registrar cron real",
    "registrar systemd timer real",
    "registrar Windows Task Scheduler real",
    "criar servico operacional",
    "executar orquestrador automaticamente nesta branch",
    "executar builders reais por padrao",
    "alterar Freqtrade",
    "alterar RiskManager",
    "alterar Qlib runtime",
    "alterar IA Shadow runtime",
    "alterar modelos",
    "alterar datasets operacionais",
    "habilitar live",
    "habilitar canary",
    "enviar ordem real",
    "usar exchange privada",
    "escrever artefatos em data/runtime/reports/logs/freqtrade",
    "usar scheduler para liberar operacao",
    "treinar modelo nesta branch",
    "promover modelo",
    "promover regra candidata",
    "aplicar candidate rule",
    "aplicar feedback na IA Shadow",
)

ALLOWED_NEXT_STEPS = (
    "criar dashboard daily learning command center em branch futura",
    "criar evidence readiness integration em branch futura",
    "criar treinamento Qlib research-only em branch futura",
    "executar revisao manual antes de qualquer registro real de scheduler",
    "materializar payloads research-only fora de runtime somente com output explicito",
)

REQUIRED_FALSE_FLAGS = (
    "live_release_allowed",
    "live_trading_enabled",
    "canary_release_allowed",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "operational_authority",
    "sends_orders",
    "changes_risk",
    "changes_model",
    "runs_training",
    "runs_ocr",
    "runs_ai_shadow_incremental",
    "updates_qlib_runtime",
    "updates_ai_shadow_runtime",
    "updates_ai_shadow_policy",
    "updates_ai_shadow_thresholds",
    "updates_freqtrade",
    "updates_risk_manager",
    "updates_models",
    "applies_shadow_rules",
    "applies_feedback_to_ai_shadow",
    "can_promote_model",
    "can_promote_rules",
    "promotes_shadow_rules",
    "writes_data",
    "writes_runtime",
    "writes_reports",
    "writes_parquet",
    "writes_sqlite",
    "writes_ai_shadow_sqlite",
    "creates_cron",
    "creates_systemd_timer",
    "creates_windows_task",
    "creates_service",
    "registers_scheduler",
    "executes_orchestrator",
    "executes_stage_builders",
    "modifies_project_scheduler",
    "modifies_operational_runtime",
)

REQUIRED_TRUE_FLAGS = ("paper_only", "shadow_only", "research_only", "read_only")


class SchedulerContractError(ValueError):
    """Raised when an invalid scheduler contract parameter is provided."""


@dataclass(frozen=True)
class SchedulerTime:
    """UTC schedule time for a paper-only contract."""

    hour_utc: int = 3
    minute_utc: int = 15

    def validate(self) -> None:
        if not 0 <= self.hour_utc <= 23:
            raise SchedulerContractError("hour_utc must be in the inclusive range 0..23")
        if not 0 <= self.minute_utc <= 59:
            raise SchedulerContractError("minute_utc must be in the inclusive range 0..59")

    @property
    def hhmm(self) -> str:
        self.validate()
        return f"{self.hour_utc:02d}:{self.minute_utc:02d}"

    @property
    def iso_time(self) -> str:
        self.validate()
        return time(hour=self.hour_utc, minute=self.minute_utc).isoformat(timespec="minutes")


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return default if value is None else int(value)
    except (TypeError, ValueError):
        return default


def output_path_is_forbidden(project_root: str | Path, output_path: str | Path | None) -> bool:
    """Return True when the output target is under operational/runtime roots."""

    if output_path is None:
        return False

    root = Path(project_root).resolve()
    target = Path(output_path).resolve()
    try:
        relative = target.relative_to(root)
    except ValueError:
        return False

    parts = tuple(part.lower() for part in relative.parts)
    return bool(parts) and parts[0] in FORBIDDEN_OUTPUT_ROOTS


def build_safe_orchestrator_command(project_root: str | Path | None = None) -> dict[str, Any]:
    """Build a non-operational command contract for manual review."""

    root_value = "." if project_root is None else str(project_root)
    args = [
        "python",
        ORCHESTRATOR_SCRIPT,
        "--project-root",
        root_value,
        "--no-write",
        "--json",
    ]
    return {
        "command_kind": "manual_review_command_only",
        "command_args": args,
        "command_display": " ".join(args),
        "script": ORCHESTRATOR_SCRIPT,
        "contains_no_write": "--no-write" in args,
        "contains_json": "--json" in args,
        "executes_now": False,
        "safe_for_copy_review": True,
    }


def summarize_orchestrator_payload(orchestrator_payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Extract a compact safe summary from a Branch 13 orchestrator payload."""

    payload = _as_dict(orchestrator_payload)
    if not payload:
        return {
            "payload_provided": False,
            "schema_version": None,
            "status": "not_loaded",
            "decision": DECISION_KEEP_RESEARCH,
            "stage_count": 0,
            "failed_stage_count": 0,
            "unsafe_stage_count": 0,
            "write_performed": False,
            "safe_for_scheduler_contract": True,
        }

    stage_summary = _as_dict(payload.get("stage_summary"))
    failed_count = _safe_int(stage_summary.get("failed_stage_count"), 0)
    unsafe_count = _safe_int(stage_summary.get("unsafe_stage_count"), 0)
    write_performed = bool(payload.get("write_performed", False))
    operational_authority = bool(payload.get("operational_authority", False))
    safe_for_contract = (
        failed_count == 0
        and unsafe_count == 0
        and write_performed is False
        and operational_authority is False
        and payload.get("decision") == DECISION_KEEP_RESEARCH
    )
    return {
        "payload_provided": True,
        "schema_version": payload.get("schema_version"),
        "status": payload.get("status", "unknown"),
        "decision": payload.get("decision", DECISION_KEEP_RESEARCH),
        "stage_count": _safe_int(stage_summary.get("stage_count"), 0),
        "not_executed_stage_count": _safe_int(stage_summary.get("not_executed_stage_count"), 0),
        "failed_stage_count": failed_count,
        "unsafe_stage_count": unsafe_count,
        "write_performed": write_performed,
        "operational_authority": operational_authority,
        "safe_for_scheduler_contract": safe_for_contract,
    }


def build_schedule_contract(
    *,
    project_root: str | Path | None = None,
    hour_utc: int = 3,
    minute_utc: int = 15,
) -> dict[str, Any]:
    """Build the deterministic paper scheduler contract."""

    schedule_time = SchedulerTime(hour_utc=hour_utc, minute_utc=minute_utc)
    schedule_time.validate()
    command = build_safe_orchestrator_command(project_root)

    return {
        "contract_kind": "paper_daily_learning_scheduler_contract",
        "scheduler_status": "contract_only_not_registered",
        "schedule_registration_status": "not_registered",
        "cadence": "DAILY",
        "timezone": "UTC",
        "time_utc": schedule_time.hhmm,
        "iso_time_utc": schedule_time.iso_time,
        "recommended_trigger_window": "after_paper_daily_rollup",
        "jitter_policy": {"jitter_enabled": False, "reason": "deterministic_research_contract"},
        "retry_policy": {"retry_enabled": False, "reason": "no_real_scheduler_registered_in_this_branch"},
        "concurrency_policy": {
            "single_flight_required": True,
            "lock_created_by_this_branch": False,
            "reason": "contract_only_scheduler_does_not_create_runtime_locks",
        },
        "command_contract": command,
        "registration_targets": {
            "cron": False,
            "systemd_timer": False,
            "windows_task_scheduler": False,
            "docker_service": False,
            "github_actions_schedule": False,
        },
        "execution_targets": {
            "orchestrator_execution": False,
            "stage_builder_execution": False,
            "model_training": False,
            "runtime_update": False,
            "order_submission": False,
        },
    }


def build_preflight_checks() -> list[dict[str, Any]]:
    """Return deterministic preflight checks for future manual scheduler review."""

    checks = [
        ("git_clean_worktree_required", "git status --short must be empty before any future manual scheduler execution"),
        ("dev_branch_required_for_manual_review", "manual review should run from clean dev unless a reviewed release branch is selected"),
        ("manifest_current_required", "python scripts/generate_project_manifest.py --check must return manifest_current"),
        ("secret_scan_required", "python scripts/scan_versioned_secrets.py --project-root . --json must return status ok"),
        ("orchestrator_no_write_required", "orchestrator command must include --no-write and --json"),
        ("no_runtime_output_required", "scheduler contract must not write under data/runtime/reports/logs/freqtrade"),
    ]
    return [
        {"check_id": check_id, "description": description, "required": True, "enforced_by_this_branch": False, "status": "documented_only"}
        for check_id, description in checks
    ]


def build_run_plan(
    *,
    project_root: str | Path | None = None,
    hour_utc: int = 3,
    minute_utc: int = 15,
    orchestrator_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a non-executing run plan for the Daily Learning scheduler."""

    contract = build_schedule_contract(project_root=project_root, hour_utc=hour_utc, minute_utc=minute_utc)
    orchestrator_summary = summarize_orchestrator_payload(orchestrator_payload)
    steps = [
        {
            "sequence": 1,
            "step_id": "preflight_static_safety",
            "step_name": "Validate static safety flags",
            "status": "planned_not_executed",
            "executes_code": False,
            "writes_output": False,
        },
        {
            "sequence": 2,
            "step_id": "manual_orchestrator_no_write_command",
            "step_name": "Review orchestrator no-write command",
            "status": "planned_not_executed",
            "executes_code": False,
            "writes_output": False,
            "command_args": contract["command_contract"]["command_args"],
        },
        {
            "sequence": 3,
            "step_id": "review_research_payload",
            "step_name": "Review research-only payload outside operational runtime",
            "status": "planned_not_executed",
            "executes_code": False,
            "writes_output": False,
        },
        {
            "sequence": 4,
            "step_id": "operator_manual_decision",
            "step_name": "Operator manual no-go/go review",
            "status": "blocked_not_requested",
            "executes_code": False,
            "writes_output": False,
        },
    ]
    return {
        "run_plan_kind": "paper_scheduler_contract_run_plan",
        "contract": contract,
        "preflight_checks": build_preflight_checks(),
        "orchestrator_payload_summary": orchestrator_summary,
        "steps": steps,
        "step_count": len(steps),
        "planned_not_executed_step_count": sum(1 for step in steps if step["status"].endswith("not_executed")),
        "blocked_step_count": sum(1 for step in steps if step["status"].startswith("blocked")),
        "executes_any_step": any(bool(step["executes_code"]) for step in steps),
        "writes_any_output": any(bool(step["writes_output"]) for step in steps),
    }


def build_scheduler_scope() -> dict[str, bool]:
    """Return explicit scheduler capabilities and prohibitions."""

    return {
        "defines_paper_scheduler_contract": True,
        "builds_daily_run_plan": True,
        "points_to_daily_learning_orchestrator": True,
        "paper_scheduler_only": True,
        "research_scheduler_only": True,
        "read_only_scheduler_contract": True,
        "loads_runtime_sources_by_default": False,
        "executes_orchestrator": False,
        "executes_stage_builders": False,
        "runs_training": False,
        "updates_qlib_runtime": False,
        "updates_ai_shadow_runtime": False,
        "updates_freqtrade": False,
        "updates_risk_manager": False,
        "updates_models": False,
        "applies_shadow_rules": False,
        "applies_feedback_to_ai_shadow": False,
        "promotes_model": False,
        "promotes_rules": False,
        "creates_cron": False,
        "creates_systemd_timer": False,
        "creates_windows_task": False,
        "creates_service": False,
        "registers_scheduler": False,
        "modifies_project_scheduler": False,
        "modifies_operational_runtime": False,
        "writes_data": False,
        "writes_runtime": False,
        "writes_reports": False,
        "writes_project_artifacts_by_default": False,
        "uses_only_in_memory_inputs": True,
    }


def build_operator_decision() -> dict[str, bool | str]:
    """Return the operator governance decision for the scheduler branch."""

    return {
        "final_decision": FINAL_OPERATOR_DECISION,
        "scheduler_registration_allowed": False,
        "daily_learning_loop_operational_application_allowed": False,
        "orchestrator_execution_allowed": False,
        "stage_builder_execution_allowed": False,
        "training_allowed": False,
        "model_promotion_allowed": False,
        "shadow_rule_promotion_allowed": False,
        "ai_shadow_runtime_update_allowed": False,
        "qlib_runtime_update_allowed": False,
        "freqtrade_strategy_change_allowed": False,
        "risk_manager_change_allowed": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
    }


def build_readiness_policy() -> dict[str, bool]:
    """Return readiness policy clauses preserved by the scheduler contract."""

    return {
        "scheduler_contract_is_not_readiness_evidence": True,
        "scheduler_outputs_do_not_release_canary": True,
        "scheduler_outputs_do_not_release_live": True,
        "real_scheduler_registration_requires_separate_branch": True,
        "real_scheduler_registration_requires_operator_review": True,
        "real_scheduler_registration_requires_runtime_contract_binding": True,
        "model_training_requires_separate_branch": True,
        "model_promotion_requires_separate_registry_and_oos_review": True,
        "candidate_rules_require_runtime_contract_binding": True,
        "manual_go_no_go_required": True,
        "thirty_day_gap_free_soak_required_for_future_canary_review": True,
    }


def build_daily_learning_scheduler_paper_report(
    *,
    project_root: str | Path | None = None,
    orchestrator_payload: Mapping[str, Any] | None = None,
    hour_utc: int = 3,
    minute_utc: int = 15,
) -> dict[str, Any]:
    """Build the complete Branch 14 scheduler report."""

    root_value = "." if project_root is None else str(project_root)
    input_mode = "in_memory_orchestrator_payload" if orchestrator_payload else "no_runtime_rows_loaded"
    run_plan = build_run_plan(
        project_root=root_value,
        hour_utc=hour_utc,
        minute_utc=minute_utc,
        orchestrator_payload=orchestrator_payload,
    )
    scheduler_scope = build_scheduler_scope()

    report: dict[str, Any] = {
        "schema_version": DAILY_LEARNING_SCHEDULER_PAPER_SCHEMA_VERSION,
        "project_name": PROJECT_NAME,
        "project_root": root_value,
        "status": STATUS_BLOCKED,
        "reason": "daily_learning_scheduler_contract_only_without_registration_authority",
        "decision": DECISION_KEEP_RESEARCH,
        "input_mode": input_mode,
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "read_only": True,
        "operational_authority": False,
        "live_release_allowed": False,
        "live_trading_enabled": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        "changes_model": False,
        "runs_training": False,
        "runs_ocr": False,
        "runs_ai_shadow_incremental": False,
        "updates_qlib_runtime": False,
        "updates_ai_shadow_runtime": False,
        "updates_ai_shadow_policy": False,
        "updates_ai_shadow_thresholds": False,
        "updates_freqtrade": False,
        "updates_risk_manager": False,
        "updates_models": False,
        "applies_shadow_rules": False,
        "applies_feedback_to_ai_shadow": False,
        "can_apply_to_freqtrade": False,
        "can_apply_to_risk_manager": False,
        "can_promote_model": False,
        "can_promote_rules": False,
        "promotes_shadow_rules": False,
        "writes_data": False,
        "writes_runtime": False,
        "writes_reports": False,
        "writes_parquet": False,
        "writes_sqlite": False,
        "writes_ai_shadow_sqlite": False,
        "creates_cron": False,
        "creates_systemd_timer": False,
        "creates_windows_task": False,
        "creates_service": False,
        "registers_scheduler": False,
        "executes_orchestrator": False,
        "executes_stage_builders": False,
        "modifies_project_scheduler": False,
        "modifies_operational_runtime": False,
        "write_requested": False,
        "write_performed": False,
        "output_path": None,
        "scheduler_scope": scheduler_scope,
        "scheduler_contract": run_plan["contract"],
        "daily_learning_scheduler": {
            "scheduler_scope": scheduler_scope,
            "run_plan": run_plan,
            "orchestrator_payload_summary": run_plan["orchestrator_payload_summary"],
            "scheduler_quality_notes": [
                "paper_scheduler_contract_only",
                "no_real_scheduler_registered",
                "no_cron_systemd_or_windows_task_created",
                "orchestrator_command_is_no_write",
                "does_not_execute_daily_learning_loop",
                "does_not_update_runtime",
                "does_not_train_or_promote_model",
            ],
        },
        "run_plan_summary": {
            "step_count": run_plan["step_count"],
            "planned_not_executed_step_count": run_plan["planned_not_executed_step_count"],
            "blocked_step_count": run_plan["blocked_step_count"],
            "executes_any_step": run_plan["executes_any_step"],
            "writes_any_output": run_plan["writes_any_output"],
        },
        "operator_decision": build_operator_decision(),
        "readiness_policy": build_readiness_policy(),
        "forbidden_actions": list(FORBIDDEN_ACTIONS),
        "allowed_next_steps": list(ALLOWED_NEXT_STEPS),
        "validation_errors": [],
    }
    report["validation_errors"] = validate_daily_learning_scheduler_paper_report(report)
    return report


def validate_daily_learning_scheduler_paper_report(payload: Mapping[str, Any]) -> list[str]:
    """Validate hard safety invariants for a scheduler paper report."""

    errors: list[str] = []
    data = _as_dict(payload)

    if data.get("schema_version") != DAILY_LEARNING_SCHEDULER_PAPER_SCHEMA_VERSION:
        errors.append("invalid_schema_version")
    if data.get("project_name") != PROJECT_NAME:
        errors.append("invalid_project_name")
    if data.get("status") != STATUS_BLOCKED:
        errors.append("status_must_remain_blocked")
    if data.get("decision") != DECISION_KEEP_RESEARCH:
        errors.append("decision_must_remain_manter_em_research")

    for flag in REQUIRED_TRUE_FLAGS:
        if data.get(flag) is not True:
            errors.append(f"{flag}_must_be_true")

    for flag in REQUIRED_FALSE_FLAGS:
        if data.get(flag) is not False:
            errors.append(f"{flag}_must_be_false")

    scheduler_scope = _as_dict(data.get("scheduler_scope"))
    for flag in (
        "executes_orchestrator",
        "executes_stage_builders",
        "creates_cron",
        "creates_systemd_timer",
        "creates_windows_task",
        "creates_service",
        "registers_scheduler",
        "writes_data",
        "writes_runtime",
        "writes_reports",
        "runs_training",
        "updates_qlib_runtime",
        "updates_ai_shadow_runtime",
        "updates_freqtrade",
        "updates_risk_manager",
    ):
        if scheduler_scope.get(flag) is not False:
            errors.append(f"scheduler_scope_{flag}_must_be_false")

    command_contract = _as_dict(_as_dict(data.get("scheduler_contract")).get("command_contract"))
    command_args = _as_list(command_contract.get("command_args"))
    if "--no-write" not in command_args:
        errors.append("orchestrator_command_must_include_no_write")
    if "--json" not in command_args:
        errors.append("orchestrator_command_must_include_json")
    if command_contract.get("executes_now") is not False:
        errors.append("command_contract_executes_now_must_be_false")

    run_plan_summary = _as_dict(data.get("run_plan_summary"))
    if run_plan_summary.get("executes_any_step") is not False:
        errors.append("run_plan_must_not_execute_steps")
    if run_plan_summary.get("writes_any_output") is not False:
        errors.append("run_plan_must_not_write_outputs")

    operator_decision = _as_dict(data.get("operator_decision"))
    if operator_decision.get("scheduler_registration_allowed") is not False:
        errors.append("operator_must_block_scheduler_registration")
    if operator_decision.get("final_decision") != FINAL_OPERATOR_DECISION:
        errors.append("invalid_operator_final_decision")

    return errors


__all__ = [
    "DAILY_LEARNING_SCHEDULER_PAPER_SCHEMA_VERSION",
    "PROJECT_NAME",
    "STATUS_BLOCKED",
    "DECISION_KEEP_RESEARCH",
    "SchedulerContractError",
    "SchedulerTime",
    "build_daily_learning_scheduler_paper_report",
    "build_operator_decision",
    "build_preflight_checks",
    "build_readiness_policy",
    "build_run_plan",
    "build_safe_orchestrator_command",
    "build_schedule_contract",
    "build_scheduler_scope",
    "output_path_is_forbidden",
    "summarize_orchestrator_payload",
    "validate_daily_learning_scheduler_paper_report",
]
