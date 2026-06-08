from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePath, PureWindowsPath
from typing import Any


DEFAULT_TASK_NAME = "SmartCripto_AI_Daily_Update"
DEFAULT_REPORT = Path("data/reports/ai_shadow_daily_update_scheduler_audit_report.json")
DEFAULT_DAILY_SUMMARY = Path("data/reports/daily_ai_shadow_update_summary.json")
DAILY_SCRIPT_NAME = "RUN_DAILY_AI_SHADOW_UPDATE.ps1"
OLD_PATH_MARKERS = (
    "smart cripto/smartcripto_criptoai100",
    "smart cripto\\smartcripto_criptoai100",
)


def safety_flags() -> dict[str, bool]:
    return {
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "changes_risk": False,
        "model_promoted": False,
    }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip().strip('"').strip("'")


def normalize_path_like(value: object) -> str:
    text = normalize_text(value)
    return text.replace("\\", "/").rstrip("/").casefold()


def contains_normalized_path(haystack: object, needle: object) -> bool:
    haystack_text = normalize_path_like(haystack)
    needle_text = normalize_path_like(str(needle))
    return bool(needle_text and needle_text in haystack_text)


def resolve_project_root(value: str) -> PurePath:
    if re.match(r"^[A-Za-z]:[\\/]", value):
        if platform.system() == "Windows":
            return Path(value).resolve()
        return PureWindowsPath(value)
    return Path(value).resolve()


def expected_daily_script(project_root: PurePath) -> PurePath:
    return project_root / "scripts" / DAILY_SCRIPT_NAME


def parse_last_task_result(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def has_old_path(*values: object) -> bool:
    combined = " ".join(normalize_path_like(value) for value in values)
    return any(marker in combined for marker in OLD_PATH_MARKERS)


def as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "sim", "ok"}
    return bool(value)


def flatten_scheduler_evidence(raw: dict[str, Any]) -> dict[str, Any]:
    actions = raw.get("actions")
    action = raw.get("action") or {}
    if isinstance(actions, list) and actions:
        action = actions[0]
    elif isinstance(actions, dict):
        action = actions

    trigger_daily = raw.get("trigger_daily")
    if trigger_daily is None:
        triggers = raw.get("triggers") or []
        if isinstance(triggers, dict):
            triggers = [triggers]
        trigger_daily = any(
            "daily" in normalize_text(trigger.get("ScheduleType") or trigger.get("type") or trigger).casefold()
            for trigger in triggers
        )

    last_task_result = raw.get("last_task_result")
    info = raw.get("info") or {}
    if last_task_result is None and isinstance(info, dict):
        last_task_result = info.get("LastTaskResult")

    return {
        "task_name": raw.get("task_name") or raw.get("TaskName"),
        "task_exists": as_bool(raw.get("task_exists", raw.get("exists", False))),
        "action_execute": raw.get("action_execute") or action.get("Execute"),
        "action_arguments": raw.get("action_arguments") or action.get("Arguments"),
        "working_directory": raw.get("working_directory") or action.get("WorkingDirectory"),
        "trigger_daily": as_bool(trigger_daily),
        "last_run_time": raw.get("last_run_time") or info.get("LastRunTime"),
        "next_run_time": raw.get("next_run_time") or info.get("NextRunTime"),
        "last_task_result": parse_last_task_result(last_task_result),
    }


def classify_scheduler_evidence(raw: dict[str, Any], *, project_root: PurePath, task_name: str) -> dict[str, Any]:
    evidence = flatten_scheduler_evidence(raw)
    action_execute = evidence["action_execute"]
    action_arguments = evidence["action_arguments"]
    working_directory = evidence["working_directory"]
    last_task_result = evidence["last_task_result"]
    expected_script = expected_daily_script(project_root)

    task_exists = bool(evidence["task_exists"])
    stale_path = has_old_path(action_execute, action_arguments, working_directory)
    points_to_root = contains_normalized_path(working_directory, project_root) or contains_normalized_path(
        action_arguments,
        project_root,
    )
    points_to_daily_script = (
        DAILY_SCRIPT_NAME.casefold() in normalize_text(action_arguments).casefold()
        and contains_normalized_path(action_arguments, expected_script)
    )
    last_result_ok = last_task_result == 0
    trigger_daily = bool(evidence["trigger_daily"])

    if not task_exists:
        scheduler_state = "scheduler_missing"
        status = "blocked"
        reason = "scheduled_task_missing"
    elif stale_path or (last_task_result is not None and not last_result_ok):
        scheduler_state = "scheduler_broken"
        status = "blocked"
        reason = "stale_or_failed_scheduler"
    elif points_to_root and points_to_daily_script and trigger_daily:
        scheduler_state = "scheduler_configured"
        status = "ok"
        reason = "scheduler_configured"
    else:
        scheduler_state = "scheduler_misconfigured"
        status = "blocked"
        reason = "scheduler_points_to_unexpected_target"

    return {
        "status": status,
        "reason": reason,
        "scheduler_state": scheduler_state,
        "task_name": normalize_text(evidence["task_name"]) or task_name,
        "task_exists": task_exists,
        "action_execute": normalize_text(action_execute),
        "action_arguments": normalize_text(action_arguments),
        "working_directory": normalize_text(working_directory),
        "trigger_daily": trigger_daily,
        "last_run_time": evidence["last_run_time"],
        "next_run_time": evidence["next_run_time"],
        "last_task_result": last_task_result,
        "points_to_current_project_root": points_to_root,
        "points_to_run_daily_ai_shadow_update_ps1": points_to_daily_script,
        "stale_or_old_path_detected": stale_path,
        "last_result_ok": last_result_ok,
        "expected_project_root": str(project_root),
        "expected_daily_script": str(expected_script),
    }


def nested_get(payload: dict[str, Any], *keys: str) -> Any:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def is_false(value: object) -> bool:
    if value is False:
        return True
    if isinstance(value, str):
        return value.strip().casefold() in {"false", "0", "no", "nao", "não"}
    return False


def classify_daily_update_summary(summary: dict[str, Any] | None) -> dict[str, Any]:
    if not summary:
        return {
            "daily_update_classification": "daily_update_summary_missing",
            "daily_training_classification": "daily_training_not_performed",
            "daily_update_ok": False,
            "daily_training_performed": False,
            "new_rows_scored": None,
            "inserted": None,
            "model_promoted": False,
            "registry_updated": False,
            "summary_status": None,
            "summary_mode": None,
        }
    if summary.get("__invalid_json__") is True:
        return {
            "daily_update_classification": "daily_update_summary_invalid_json",
            "daily_training_classification": "daily_training_not_performed",
            "daily_update_ok": False,
            "daily_training_performed": False,
            "new_rows_scored": None,
            "inserted": None,
            "model_promoted": False,
            "registry_updated": False,
            "summary_status": None,
            "summary_mode": None,
            "json_error": summary.get("error"),
            "json_path": summary.get("path"),
        }

    policy_allowed = nested_get(summary, "policy", "allowed_mode")
    policy_sends_orders = nested_get(summary, "policy", "sends_orders")
    policy_changes_risk = nested_get(summary, "policy", "changes_risk")
    incremental_sends_orders = nested_get(summary, "incremental_shadow", "safety", "sends_orders")
    incremental_changes_risk = nested_get(summary, "incremental_shadow", "safety", "changes_risk")
    sqlite_sends_orders = nested_get(summary, "sqlite_audit", "safety", "sends_orders")
    sqlite_changes_risk = nested_get(summary, "sqlite_audit", "safety", "changes_risk")

    score_and_log_only = policy_allowed == "score_and_log_only"
    safe_score_log = all(
        is_false(value)
        for value in [
            policy_sends_orders,
            policy_changes_risk,
            incremental_sends_orders,
            incremental_changes_risk,
            sqlite_sends_orders,
            sqlite_changes_risk,
        ]
    )
    update_ok = summary.get("status") == "ok" and summary.get("mode") == "daily_ai_shadow_update" and score_and_log_only and safe_score_log

    training_section = summary.get("training") or summary.get("trainer") or {}
    daily_training_performed = bool(
        isinstance(training_section, dict)
        and training_section.get("status") == "ok"
        and (training_section.get("model_path") or training_section.get("metadata_path"))
    )
    model_promoted = bool(summary.get("model_promoted") or nested_get(summary, "registry", "model_promoted"))
    registry_updated = bool(summary.get("registry_updated") or nested_get(summary, "registry", "updated"))

    return {
        "daily_update_classification": "daily_shadow_update_ok" if update_ok else "daily_shadow_update_blocked",
        "daily_training_classification": "daily_training_performed"
        if daily_training_performed
        else "daily_training_not_performed",
        "daily_update_ok": update_ok,
        "daily_training_performed": daily_training_performed,
        "new_rows_scored": nested_get(summary, "incremental_shadow", "new_rows_scored"),
        "inserted": nested_get(summary, "incremental_shadow", "inserted"),
        "model_promoted": model_promoted,
        "registry_updated": registry_updated,
        "summary_status": summary.get("status"),
        "summary_mode": summary.get("mode"),
        "policy_allowed_mode": policy_allowed,
        "policy_sends_orders": policy_sends_orders,
        "policy_changes_risk": policy_changes_risk,
        "incremental_shadow_sends_orders": incremental_sends_orders,
        "incremental_shadow_changes_risk": incremental_changes_risk,
        "sqlite_audit_sends_orders": sqlite_sends_orders,
        "sqlite_audit_changes_risk": sqlite_changes_risk,
    }


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return {
            "__invalid_json__": True,
            "path": str(path),
            "error": str(exc),
        }


def query_windows_scheduled_task(task_name: str) -> dict[str, Any]:
    command = f"""
$task = Get-ScheduledTask -TaskName '{task_name}' -ErrorAction SilentlyContinue
if ($null -eq $task) {{
  [ordered]@{{ task_name = '{task_name}'; task_exists = $false }} | ConvertTo-Json -Depth 10
}} else {{
  $info = Get-ScheduledTaskInfo -TaskName '{task_name}'
  [ordered]@{{
    task_name = '{task_name}'
    task_exists = $true
    actions = @($task.Actions | ForEach-Object {{ [ordered]@{{
      Execute = $_.Execute
      Arguments = $_.Arguments
      WorkingDirectory = $_.WorkingDirectory
    }} }})
    triggers = @($task.Triggers | ForEach-Object {{ [ordered]@{{
      type = $_.CimClass.CimClassName
      ScheduleType = if ($_.CimClass.CimClassName -like '*Daily*') {{ 'Daily' }} else {{ $_.CimClass.CimClassName }}
    }} }})
    info = [ordered]@{{
      LastRunTime = $info.LastRunTime
      NextRunTime = $info.NextRunTime
      LastTaskResult = $info.LastTaskResult
    }}
  }} | ConvertTo-Json -Depth 10
}}
"""
    # Fixed local OS scheduler query, no shell interpolation and no secrets.
    completed = subprocess.run(  # nosec B603,B607
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def build_report(
    *,
    project_root: PurePath,
    task_name: str,
    daily_summary_path: Path,
    scheduler_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    if scheduler_payload is None and platform.system() != "Windows":
        scheduler_report = {
            "status": "unsupported_platform",
            "reason": "windows_scheduler_unavailable",
            "scheduler_state": "unsupported_platform",
            "task_name": task_name,
            "task_exists": False,
            "action_execute": "",
            "action_arguments": "",
            "working_directory": "",
            "trigger_daily": False,
            "last_run_time": None,
            "next_run_time": None,
            "last_task_result": None,
            "points_to_current_project_root": False,
            "points_to_run_daily_ai_shadow_update_ps1": False,
            "stale_or_old_path_detected": False,
            "last_result_ok": False,
            "expected_project_root": str(project_root),
            "expected_daily_script": str(expected_daily_script(project_root)),
        }
    else:
        payload = scheduler_payload if scheduler_payload is not None else query_windows_scheduled_task(task_name)
        scheduler_report = classify_scheduler_evidence(payload, project_root=project_root, task_name=task_name)

    daily_summary = load_json(daily_summary_path)
    daily_report = classify_daily_update_summary(daily_summary)

    status = scheduler_report["status"]
    reason = scheduler_report["reason"]
    if status == "ok" and not daily_report["daily_update_ok"]:
        status = "blocked"
        reason = "daily_update_summary_invalid_or_missing"

    return {
        "status": status,
        "reason": reason,
        "generated_at_utc": utc_now(),
        "project_root": str(project_root),
        "daily_summary_path": str(daily_summary_path),
        **scheduler_report,
        **daily_report,
        **safety_flags(),
    }


def write_report(report_path: Path, report: dict[str, Any]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit Windows scheduler evidence for Daily AI Shadow Update.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--task-name", default=DEFAULT_TASK_NAME)
    parser.add_argument("--daily-summary", default=str(DEFAULT_DAILY_SUMMARY))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--scheduler-json", default=None, help="Optional fixture/source JSON for deterministic audits.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project_root = resolve_project_root(args.project_root)
    daily_summary_path = Path(args.daily_summary)
    report_path = Path(args.report)
    scheduler_payload = load_json(Path(args.scheduler_json)) if args.scheduler_json else None
    report = build_report(
        project_root=project_root,
        task_name=args.task_name,
        daily_summary_path=daily_summary_path,
        scheduler_payload=scheduler_payload,
    )
    write_report(report_path, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
