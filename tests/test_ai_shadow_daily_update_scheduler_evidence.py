from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "audit_ai_shadow_daily_update_scheduler.py"
REGISTER_PATH = ROOT / "scripts" / "register_ai_shadow_daily_update_task.ps1"
OLD_PROJECT_ROOT = r"C:\Smart Cripto\SmartCripto_CriptoAI100"
CURRENT_PROJECT_ROOT = Path("E:/FUTUROS")


def load_audit_module():
    spec = importlib.util.spec_from_file_location("audit_ai_shadow_daily_update_scheduler", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def daily_summary(*, new_rows_scored: int = 0, inserted: int = 0) -> dict:
    return {
        "status": "ok",
        "mode": "daily_ai_shadow_update",
        "incremental_shadow": {
            "new_rows_scored": new_rows_scored,
            "inserted": inserted,
            "safety": {
                "sends_orders": False,
                "changes_risk": False,
            },
        },
        "sqlite_audit": {
            "safety": {
                "sends_orders": False,
                "changes_risk": False,
            },
        },
        "policy": {
            "allowed_mode": "score_and_log_only",
            "sends_orders": False,
            "changes_risk": False,
        },
    }


def scheduler_payload(
    *,
    project_root: Path = CURRENT_PROJECT_ROOT,
    last_task_result: int = 0,
    old_path: bool = False,
) -> dict:
    if old_path:
        root_text = OLD_PROJECT_ROOT
    else:
        root_text = str(project_root).replace("/", "\\")

    return {
        "task_name": "SmartCripto_AI_Daily_Update",
        "task_exists": True,
        "action_execute": "powershell.exe",
        "action_arguments": (
            "-NoProfile -ExecutionPolicy Bypass "
            f'-File "{root_text}\\scripts\\RUN_DAILY_AI_SHADOW_UPDATE.ps1"'
        ),
        "working_directory": root_text,
        "trigger_daily": True,
        "last_run_time": "2026-06-08T00:00:00-03:00",
        "next_run_time": "2026-06-09T00:00:00-03:00",
        "last_task_result": last_task_result,
    }


def test_register_script_has_current_daily_action_and_no_old_path() -> None:
    text = REGISTER_PATH.read_text(encoding="utf-8")

    assert OLD_PROJECT_ROOT not in text
    assert "RUN_DAILY_AI_SHADOW_UPDATE.ps1" in text
    assert "powershell.exe" in text
    assert "-NoProfile -ExecutionPolicy Bypass -File" in text
    assert "New-ScheduledTaskTrigger -Daily" in text
    assert "ORDER_SUBMISSION_ENABLED" not in text
    assert "REAL_ORDER_SUBMISSION_ENABLED" not in text


def test_audit_preserves_paper_shadow_only_safety_flags(tmp_path: Path) -> None:
    module = load_audit_module()
    summary_path = tmp_path / "reports" / "daily_ai_shadow_update_summary.json"
    summary_path.parent.mkdir(parents=True)
    summary_path.write_text(json.dumps(daily_summary()), encoding="utf-8")

    report = module.build_report(
        project_root=CURRENT_PROJECT_ROOT,
        task_name="SmartCripto_AI_Daily_Update",
        daily_summary_path=summary_path,
        scheduler_payload=scheduler_payload(),
    )

    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["live_trading_enabled"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False
    assert report["sends_orders"] is False
    assert report["exchange_private_access"] is False
    assert report["changes_risk"] is False


def test_score_and_log_only_is_update_ok_not_daily_training() -> None:
    module = load_audit_module()

    classification = module.classify_daily_update_summary(daily_summary(new_rows_scored=0, inserted=0))

    assert classification["daily_update_classification"] == "daily_shadow_update_ok"
    assert classification["daily_training_classification"] == "daily_training_not_performed"
    assert classification["daily_update_ok"] is True
    assert classification["daily_training_performed"] is False
    assert classification["new_rows_scored"] == 0
    assert classification["inserted"] == 0
    assert classification["model_promoted"] is False
    assert classification["registry_updated"] is False


def test_nonzero_last_task_result_classifies_scheduler_broken() -> None:
    module = load_audit_module()

    report = module.classify_scheduler_evidence(
        scheduler_payload(last_task_result=2147942667),
        project_root=CURRENT_PROJECT_ROOT,
        task_name="SmartCripto_AI_Daily_Update",
    )

    assert report["status"] == "blocked"
    assert report["scheduler_state"] == "scheduler_broken"
    assert report["reason"] == "stale_or_failed_scheduler"
    assert report["last_task_result"] == 2147942667
    assert report["last_result_ok"] is False


def test_old_action_path_sets_stale_or_old_path_detected() -> None:
    module = load_audit_module()

    report = module.classify_scheduler_evidence(
        scheduler_payload(old_path=True),
        project_root=CURRENT_PROJECT_ROOT,
        task_name="SmartCripto_AI_Daily_Update",
    )

    assert report["status"] == "blocked"
    assert report["scheduler_state"] == "scheduler_broken"
    assert report["stale_or_old_path_detected"] is True
    assert report["points_to_current_project_root"] is False


def test_current_project_action_classifies_scheduler_configured() -> None:
    module = load_audit_module()

    report = module.classify_scheduler_evidence(
        scheduler_payload(),
        project_root=CURRENT_PROJECT_ROOT,
        task_name="SmartCripto_AI_Daily_Update",
    )

    assert report["status"] == "ok"
    assert report["scheduler_state"] == "scheduler_configured"
    assert report["points_to_current_project_root"] is True
    assert report["points_to_run_daily_ai_shadow_update_ps1"] is True
    assert report["stale_or_old_path_detected"] is False
    assert report["last_result_ok"] is True


def test_cli_uses_fixture_without_touching_project_runtime(tmp_path: Path, capsys) -> None:
    module = load_audit_module()
    scheduler_path = tmp_path / "scheduler.json"
    daily_summary_path = tmp_path / "reports" / "daily_ai_shadow_update_summary.json"
    report_path = tmp_path / "reports" / "ai_shadow_daily_update_scheduler_audit_report.json"
    daily_summary_path.parent.mkdir(parents=True)
    scheduler_path.write_text(json.dumps(scheduler_payload()), encoding="utf-8")
    daily_summary_path.write_text(json.dumps(daily_summary()), encoding="utf-8")

    exit_code = module.main(
        [
            "--project-root",
            str(CURRENT_PROJECT_ROOT),
            "--scheduler-json",
            str(scheduler_path),
            "--daily-summary",
            str(daily_summary_path),
            "--report",
            str(report_path),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["scheduler_state"] == "scheduler_configured"
    assert payload["daily_update_classification"] == "daily_shadow_update_ok"
    assert payload["daily_training_classification"] == "daily_training_not_performed"
    assert report_path.exists()
