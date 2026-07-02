from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from smartcrypto.learning.paper_autolearning.scheduler_deployment import (
    EXPECTED_SERVICE_COMMAND,
    SERVICE_NAME,
    build_paper_autolearning_scheduler_deployment_report,
)


def write_compose(root: Path, *, command: list[str] | None = None) -> Path:
    compose = {
        "services": {
            SERVICE_NAME: {
                "build": {"context": ".", "dockerfile": "docker/smartcrypto/Dockerfile"},
                "restart": "no",
                "profiles": ["autolearning"],
                "environment": {
                    "SMARTCRYPTO_RUNTIME_MODE": "paper",
                    "LIVE_ENABLED": "false",
                    "ORDER_SUBMISSION_ENABLED": "false",
                    "REAL_ORDER_SUBMISSION_ENABLED": "false",
                    "SMARTCRYPTO_EXCHANGE_PRIVATE_ACCESS": "false",
                    "PYTHONPATH": "/app",
                },
                "working_dir": "/app",
                "command": command or EXPECTED_SERVICE_COMMAND,
            }
        }
    }
    path = root / "docker-compose.paper.yml"
    path.write_text(yaml.safe_dump(compose, sort_keys=False), encoding="utf-8")
    return path


def write_kill_switch_contract(root: Path) -> Path:
    path = root / "docker" / "paper-autolearning-scheduler" / "autolearning_scheduler_kill_switch.template.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "paper_autolearning_scheduler_kill_switch_v1",
                "paper_only": True,
                "shadow_only": True,
                "live_trading_enabled": False,
                "order_submission_enabled": False,
                "real_order_submission_enabled": False,
                "exchange_private_access": False,
            }
        ),
        encoding="utf-8",
    )
    return path


def ready_report(tmp_path: Path) -> dict[str, Any]:
    compose = write_compose(tmp_path)
    kill_switch = write_kill_switch_contract(tmp_path)
    return build_paper_autolearning_scheduler_deployment_report(
        project_root=tmp_path,
        compose_path=compose,
        kill_switch_contract_path=kill_switch,
    )


def test_deployment_default_is_dry_run(tmp_path: Path) -> None:
    report = ready_report(tmp_path)

    assert report["status"] == "ok"
    assert report["deployment_status"] == "deployment_ready"
    assert report["deployment_performed"] is False
    assert report["creates_service"] is False


def test_deployment_requires_kill_switch_contract(tmp_path: Path) -> None:
    compose = write_compose(tmp_path)
    report = build_paper_autolearning_scheduler_deployment_report(
        project_root=tmp_path,
        compose_path=compose,
        kill_switch_contract_path=tmp_path / "missing.json",
    )

    assert report["status"] == "blocked"
    assert report["deployment_status"] == "blocked"
    assert report["reason"] == "kill_switch_contract_missing"
    assert report["kill_switch_required"] is True
    assert report["kill_switch_contract_present"] is False


def test_deployment_validates_foundation_runner_command(tmp_path: Path) -> None:
    report = ready_report(tmp_path)

    assert report["command_validated"] is True
    assert report["foundation_runner_command_validated"] is True
    assert report["would_run_command"] == EXPECTED_SERVICE_COMMAND


def test_deployment_does_not_create_cron_unless_selected(tmp_path: Path) -> None:
    report = ready_report(tmp_path)

    assert report["selected_mechanism"] == "docker_compose_paper"
    assert report["creates_cron"] is False
    assert report["creates_systemd_timer"] is False


def test_deployment_does_not_create_windows_task(tmp_path: Path) -> None:
    report = ready_report(tmp_path)

    assert report["creates_windows_task"] is False
    assert report["windows_task_defined"] is False


def test_deployment_does_not_start_service(tmp_path: Path) -> None:
    report = ready_report(tmp_path)

    assert report["creates_service"] is False
    assert report["deployment_performed"] is False
    assert report["writes_runtime"] is False


def test_deployment_preserves_master_update_false(tmp_path: Path) -> None:
    report = ready_report(tmp_path)

    assert report["master_update_requested"] is False
    assert report["master_update_performed"] is False


def test_deployment_preserves_no_model_promotion(tmp_path: Path) -> None:
    report = ready_report(tmp_path)

    assert report["model_promotion_performed"] is False
    assert report["active_model_changed"] is False


def test_deployment_never_sends_orders(tmp_path: Path) -> None:
    report = ready_report(tmp_path)

    assert report["sends_orders"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False


def test_deployment_never_accesses_exchange_private(tmp_path: Path) -> None:
    report = ready_report(tmp_path)

    assert report["exchange_private_access"] is False


def test_deployment_reports_logs_and_audit_paths(tmp_path: Path) -> None:
    report = ready_report(tmp_path)
    log_path = report["log_path_planned"].replace("\\", "/")
    audit_path = report["audit_report_path_planned"].replace("\\", "/")

    assert log_path.endswith("data/logs/paper_autolearning_scheduler.log")
    assert audit_path.endswith("data/reports/paper_autolearning_scheduler_deployment_audit_v1.json")


def test_cli_audit_json_executes() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/audit_paper_autolearning_scheduler_deployment_v1.py", "--project-root", ".", "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["schema_version"] == "paper_autolearning_scheduler_deployment_v1"
    assert payload["status"] == "ok"
    assert payload["deployment_status"] == "deployment_ready"
    assert payload["daily_autolearning_enabled"] is True
    assert payload["docker_service_defined"] is True
    assert payload["kill_switch_contract_present"] is True
    assert payload["foundation_runner_command_validated"] is True
    assert payload["deployment_performed"] is False
    assert payload["sends_orders"] is False
