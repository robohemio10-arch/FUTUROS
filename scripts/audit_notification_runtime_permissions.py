from __future__ import annotations

import argparse
import ast
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml


SERVICE_NAME = "trade-event-notifications-paper"
DEFAULT_COMPOSE_PATH = "docker-compose.paper.yml"
DEFAULT_REPORT_PATH = "data/reports/notification_runtime_permissions_audit.json"
BOOTSTRAP_SCRIPT = "scripts/docker_runtime_permissions_bootstrap.py"
DAEMON_SCRIPT = "scripts/run_trade_event_notifications.py"
EXPECTED_PERMISSION_PATHS = ("/app/data/reports", "/app/data/runtime")
SAFE_FLAGS = {
    "paper_only": True,
    "shadow_only": True,
    "live_trading_enabled": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
    "canary_release_allowed": False,
    "live_release_allowed": False,
}


def audit_notification_runtime_permissions(
    *,
    project_root: str | Path = ".",
    compose_path: str | Path = DEFAULT_COMPOSE_PATH,
    report_path: str | Path = DEFAULT_REPORT_PATH,
    write: bool = False,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    compose = resolve_under_root(root, compose_path)
    report = resolve_under_root(root, report_path)
    base = base_report(compose=compose, report=report, write=write)

    if not compose.is_file():
        return finalize(
            base,
            status="blocked",
            reason="docker_compose_paper_missing",
            security_recommendation="restore_versioned_compose_and_repeat_static_audit",
        )

    try:
        payload = yaml.safe_load(compose.read_text(encoding="utf-8"))
    except OSError as exc:
        return finalize(
            base,
            status="blocked",
            reason="docker_compose_paper_unreadable",
            error=f"{type(exc).__name__}: {exc}",
        )
    except yaml.YAMLError as exc:
        return finalize(
            base,
            status="blocked",
            reason="docker_compose_paper_invalid_yaml",
            error=f"{type(exc).__name__}: {exc}",
        )

    services = payload.get("services") if isinstance(payload, Mapping) else None
    service = services.get(SERVICE_NAME) if isinstance(services, Mapping) else None
    if not isinstance(service, Mapping):
        return finalize(base, status="blocked", reason="notification_service_missing")

    compose_user = str(service.get("user") or "image_default")
    runs_as_root = compose_user in {"0", "0:0", "root", "root:root"}
    command = string_sequence(service.get("command"))
    volumes = string_sequence(service.get("volumes"))
    permission_paths = command_values(command, "--path")
    bootstrap_detected = BOOTSTRAP_SCRIPT in command
    daemon_detected = DAEMON_SCRIPT in command
    separator_index = command.index("--") if "--" in command else -1
    bootstrap_index = command.index(BOOTSTRAP_SCRIPT) if BOOTSTRAP_SCRIPT in command else -1
    daemon_index = command.index(DAEMON_SCRIPT) if DAEMON_SCRIPT in command else -1
    daemon_after_bootstrap = (
        bootstrap_index >= 0
        and separator_index > bootstrap_index
        and daemon_index > separator_index
    )
    data_bind_mount = any(volume.startswith("./data:/app/data") for volume in volumes)
    bootstrap_analysis = inspect_bootstrap(root / BOOTSTRAP_SCRIPT)
    permissions_limited = set(permission_paths) == set(EXPECTED_PERMISSION_PATHS)
    privilege_drop_verified = (
        bootstrap_analysis["sets_gid"]
        and bootstrap_analysis["sets_uid"]
        and bootstrap_analysis["execs_command"]
        and bootstrap_analysis["rejects_root_identity"]
        and bootstrap_analysis["restricts_permission_paths"]
        and daemon_after_bootstrap
    )
    root_required = runs_as_root and data_bind_mount and bootstrap_detected

    base.update(
        {
            "compose_user": compose_user,
            "runs_as_root": runs_as_root,
            "root_required": root_required,
            "bootstrap_detected": bootstrap_detected,
            "bootstrap_script": BOOTSTRAP_SCRIPT if bootstrap_detected else None,
            "daemon_script": DAEMON_SCRIPT if daemon_detected else None,
            "daemon_after_bootstrap": daemon_after_bootstrap,
            "privilege_drop_verified": privilege_drop_verified,
            "permission_paths": permission_paths,
            "permission_paths_limited": permissions_limited,
            "data_bind_mount_detected": data_bind_mount,
            "bootstrap_analysis": bootstrap_analysis,
        }
    )

    if not runs_as_root:
        return finalize(
            base,
            status="ok",
            reason="notification_service_runs_non_root",
            security_recommendation="retain_non_root_runtime_and_verify_bind_mount_writes_operationally",
        )
    if not root_required:
        return finalize(
            base,
            status="blocked",
            reason="root_override_without_bind_mount_bootstrap_requirement",
            security_recommendation="remove_root_override",
        )
    if not permissions_limited or not privilege_drop_verified:
        return finalize(
            base,
            status="blocked",
            reason="root_bootstrap_not_sufficiently_restricted",
            security_recommendation="restrict_paths_and_prove_privilege_drop_before_daemon",
        )
    return finalize(
        base,
        status="ok",
        reason="temporary_root_bootstrap_justified_and_privileges_dropped",
        security_recommendation=(
            "retain_temporary_root_bootstrap_for_windows_bind_mount; migrate_writable_state_to_"
            "preowned_named_volumes_before_removing_root_override"
        ),
    )


def inspect_bootstrap(path: Path) -> dict[str, Any]:
    result = {
        "exists": path.is_file(),
        "sets_gid": False,
        "sets_uid": False,
        "execs_command": False,
        "rejects_root_identity": False,
        "restricts_permission_paths": False,
    }
    if not path.is_file():
        return result
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError, UnicodeError):
        return result
    calls = {
        dotted_name(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    result.update(
        {
            "sets_gid": "os.setgid" in calls,
            "sets_uid": "os.setuid" in calls,
            "execs_command": "os.execvp" in calls,
            "rejects_root_identity": "non_root_identifier" in source and "identifier <= 0" in source,
            "restricts_permission_paths": (
                "ALLOWED_RUNTIME_PATHS" in source
                and all(path_value in source for path_value in EXPECTED_PERMISSION_PATHS)
            ),
        }
    )
    return result


def dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def string_sequence(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [str(item) for item in value]


def command_values(command: list[str], option: str) -> list[str]:
    return [command[index + 1] for index, item in enumerate(command[:-1]) if item == option]


def base_report(*, compose: Path, report: Path, write: bool) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": "audit_not_completed",
        "service_name": SERVICE_NAME,
        "compose_path": str(compose),
        "compose_user": "unknown",
        "runs_as_root": False,
        "root_required": False,
        "bootstrap_detected": False,
        "bootstrap_script": None,
        "permission_paths": [],
        "security_recommendation": "complete_static_audit",
        "report_path": str(report),
        "write_performed": write,
        **SAFE_FLAGS,
    }


def finalize(
    report: dict[str, Any],
    *,
    status: str,
    reason: str,
    security_recommendation: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    report["status"] = status
    report["reason"] = reason
    if security_recommendation is not None:
        report["security_recommendation"] = security_recommendation
    if error is not None:
        report["error"] = error
    if report["write_performed"]:
        output = Path(report["report_path"])
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            report["status"] = "blocked"
            report["reason"] = "report_write_failed"
            report["error"] = f"{type(exc).__name__}: {exc}"
            report["write_performed"] = False
    return report


def resolve_under_root(root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path_outside_project_root:{resolved}") from exc
    return resolved


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit notification service runtime permissions read-only.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--compose", default=DEFAULT_COMPOSE_PATH)
    parser.add_argument("--report", default=DEFAULT_REPORT_PATH)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = audit_notification_runtime_permissions(
            project_root=args.project_root,
            compose_path=args.compose,
            report_path=args.report,
            write=bool(args.write),
        )
    except ValueError as exc:
        report = {
            "status": "blocked",
            "reason": "invalid_audit_path",
            "error": str(exc),
            "service_name": SERVICE_NAME,
            **SAFE_FLAGS,
        }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(f"{report['status']}: {report['reason']}")
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
