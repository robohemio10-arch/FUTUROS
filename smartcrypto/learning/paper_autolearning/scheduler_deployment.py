"""Deployment readiness contract for the paper auto-learning scheduler."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from .outcome_schema import SAFETY_FLAGS, utc_now_iso

SCHEMA_VERSION = "paper_autolearning_scheduler_deployment_v1"
SELECTED_MECHANISM = "docker_compose_paper"
SERVICE_NAME = "paper-autolearning-scheduler"
DEFAULT_COMPOSE_PATH = Path("docker-compose.paper.yml")
DEFAULT_KILL_SWITCH_CONTRACT_PATH = Path(
    "docker/paper-autolearning-scheduler/autolearning_scheduler_kill_switch.template.json"
)
DEFAULT_LOG_PATH = Path("data/logs/paper_autolearning_scheduler.log")
DEFAULT_AUDIT_REPORT_PATH = Path("data/reports/paper_autolearning_scheduler_deployment_audit_v1.json")

EXPECTED_SERVICE_COMMAND = [
    "python",
    "scripts/run_paper_autolearning_scheduler_v1.py",
    "--project-root",
    "/app",
    "--once",
    "--write-feedback",
    "--train-smoke",
    "--json",
]

DEPLOYMENT_SAFETY_FLAGS: dict[str, bool] = {
    **SAFETY_FLAGS,
    "live_trading_enabled": False,
    "creates_cron": False,
    "creates_systemd_timer": False,
    "creates_windows_task": False,
    "creates_service": False,
    "deployment_performed": False,
    "master_update_requested": False,
    "master_update_performed": False,
    "model_promotion_performed": False,
    "active_model_changed": False,
    "writes_runtime": False,
    "writes_sqlite": False,
}

REQUIRED_FALSE_SERVICE_ENV = {
    "LIVE_ENABLED": "false",
    "ORDER_SUBMISSION_ENABLED": "false",
    "REAL_ORDER_SUBMISSION_ENABLED": "false",
    "SMARTCRYPTO_EXCHANGE_PRIVATE_ACCESS": "false",
}


@dataclass(frozen=True)
class DeploymentInputs:
    """Resolved inputs used by the static deployment auditor."""

    project_root: Path
    compose_path: Path
    kill_switch_contract_path: Path


def build_paper_autolearning_scheduler_deployment_report(
    *,
    project_root: str | Path,
    compose_path: str | Path | None = None,
    kill_switch_contract_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build a static deployment-readiness report without starting services."""

    inputs = resolve_inputs(
        project_root=project_root,
        compose_path=compose_path,
        kill_switch_contract_path=kill_switch_contract_path,
    )
    compose_payload = load_compose(inputs.compose_path)
    service = get_service(compose_payload, SERVICE_NAME)
    docker_service_defined = bool(service)
    command_validated = validate_service_command(service)
    env_validated = validate_service_environment(service)
    foundation_runner_command_validated = command_validated
    kill_switch_contract_present = inputs.kill_switch_contract_path.exists()
    kill_switch_checked = True

    validation_errors = validate_deployment_components(
        docker_service_defined=docker_service_defined,
        command_validated=command_validated,
        env_validated=env_validated,
        kill_switch_contract_present=kill_switch_contract_present,
    )
    status = "blocked" if validation_errors else "ok"
    deployment_status = "blocked" if validation_errors else "deployment_ready"
    reason = reason_from_errors(validation_errors)
    daily_autolearning_enabled = not validation_errors

    report: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_now_iso(),
        "deployment_status": deployment_status,
        "deployment_mode": SELECTED_MECHANISM,
        "deployment_performed": False,
        "scheduler_enabled": daily_autolearning_enabled,
        "daily_autolearning_enabled": daily_autolearning_enabled,
        "selected_mechanism": SELECTED_MECHANISM,
        "would_run_command": list(EXPECTED_SERVICE_COMMAND),
        "command_validated": command_validated,
        "foundation_runner_command_validated": foundation_runner_command_validated,
        "kill_switch_required": True,
        "kill_switch_contract_present": kill_switch_contract_present,
        "kill_switch_checked": kill_switch_checked,
        "kill_switch_contract_path": str(inputs.kill_switch_contract_path),
        "log_path_planned": str((inputs.project_root / DEFAULT_LOG_PATH).resolve()),
        "audit_report_path_planned": str((inputs.project_root / DEFAULT_AUDIT_REPORT_PATH).resolve()),
        "creates_cron": False,
        "creates_systemd_timer": False,
        "creates_windows_task": False,
        "creates_service": False,
        "docker_service_defined": docker_service_defined,
        "systemd_unit_defined": False,
        "windows_task_defined": False,
        "master_update_requested": False,
        "master_update_performed": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
        "paper_only": True,
        "shadow_only": True,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "changes_risk": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "updates_freqtrade": False,
        "updates_risk_manager": False,
        "updates_qlib_runtime": False,
        "updates_ai_shadow_runtime": False,
        "safety_flags": dict(DEPLOYMENT_SAFETY_FLAGS),
        "validation_errors": validation_errors,
        "compose_path": str(inputs.compose_path),
        "service_name": SERVICE_NAME,
        "service_environment_validated": env_validated,
    }
    return report


def resolve_inputs(
    *,
    project_root: str | Path,
    compose_path: str | Path | None,
    kill_switch_contract_path: str | Path | None,
) -> DeploymentInputs:
    root = Path(project_root).resolve()
    compose = resolve_under_root(root, compose_path or DEFAULT_COMPOSE_PATH)
    kill_switch = resolve_under_root(root, kill_switch_contract_path or DEFAULT_KILL_SWITCH_CONTRACT_PATH)
    return DeploymentInputs(project_root=root, compose_path=compose, kill_switch_contract_path=kill_switch)


def resolve_under_root(root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate.resolve()
    return (root / candidate).resolve()


def load_compose(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    return payload if isinstance(payload, Mapping) else {}


def get_service(compose_payload: Mapping[str, Any], service_name: str) -> Mapping[str, Any]:
    services = compose_payload.get("services")
    if not isinstance(services, Mapping):
        return {}
    service = services.get(service_name)
    return service if isinstance(service, Mapping) else {}


def normalize_command(command: Any) -> list[str]:
    if isinstance(command, list):
        return [str(part) for part in command]
    if isinstance(command, str):
        return command.split()
    return []


def validate_service_command(service: Mapping[str, Any]) -> bool:
    command = normalize_command(service.get("command"))
    return command == EXPECTED_SERVICE_COMMAND


def validate_service_environment(service: Mapping[str, Any]) -> bool:
    environment = service.get("environment")
    if not isinstance(environment, Mapping):
        return False
    for key, expected in REQUIRED_FALSE_SERVICE_ENV.items():
        if str(environment.get(key, "")).lower() != expected:
            return False
    return str(environment.get("SMARTCRYPTO_RUNTIME_MODE", "")).lower() == "paper"


def validate_deployment_components(
    *,
    docker_service_defined: bool,
    command_validated: bool,
    env_validated: bool,
    kill_switch_contract_present: bool,
) -> list[str]:
    errors: list[str] = []
    if not kill_switch_contract_present:
        errors.append("kill_switch_contract_missing")
    if not docker_service_defined:
        errors.append("docker_service_missing")
    if docker_service_defined and not command_validated:
        errors.append("scheduler_command_invalid")
    if docker_service_defined and not env_validated:
        errors.append("scheduler_environment_unsafe")
    return errors


def reason_from_errors(errors: list[str]) -> str:
    if not errors:
        return "paper_autolearning_scheduler_deployment_ready"
    if "kill_switch_contract_missing" in errors:
        return "kill_switch_contract_missing"
    return errors[0]
