from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.audit_notification_runtime_permissions import (
    SAFE_FLAGS,
    audit_notification_runtime_permissions,
)
from scripts.docker_runtime_permissions_bootstrap import parse_args


ROOT = Path(__file__).resolve().parents[1]
AUDITOR = ROOT / "scripts" / "audit_notification_runtime_permissions.py"
COMPOSE = ROOT / "docker-compose.paper.yml"
BOOTSTRAP = ROOT / "scripts" / "docker_runtime_permissions_bootstrap.py"


def copy_audit_inputs(tmp_path: Path, *, compose_text: str | None = None) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "scripts").mkdir()
    shutil.copy2(BOOTSTRAP, root / "scripts" / BOOTSTRAP.name)
    (root / "docker-compose.paper.yml").write_text(
        compose_text if compose_text is not None else COMPOSE.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return root


def test_current_root_override_is_explicitly_justified_and_drops_privileges() -> None:
    report = audit_notification_runtime_permissions(project_root=ROOT)

    assert report["status"] == "ok"
    assert report["reason"] == "temporary_root_bootstrap_justified_and_privileges_dropped"
    assert report["compose_user"] == "0:0"
    assert report["runs_as_root"] is True
    assert report["root_required"] is True
    assert report["bootstrap_detected"] is True
    assert report["privilege_drop_verified"] is True
    assert report["daemon_after_bootstrap"] is True
    assert set(report["permission_paths"]) == {"/app/data/reports", "/app/data/runtime"}
    assert report["permission_paths_limited"] is True
    assert "named_volumes" in report["security_recommendation"]


def test_non_root_compose_does_not_report_root_requirement(tmp_path: Path) -> None:
    compose = COMPOSE.read_text(encoding="utf-8").replace('    user: "0:0"\n', '    user: "10001:10001"\n')
    root = copy_audit_inputs(tmp_path, compose_text=compose)

    report = audit_notification_runtime_permissions(project_root=root)

    assert report["status"] == "ok"
    assert report["reason"] == "notification_service_runs_non_root"
    assert report["runs_as_root"] is False
    assert report["root_required"] is False


def test_root_without_verified_bootstrap_is_blocked(tmp_path: Path) -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    service_start = compose.index("  trade-event-notifications-paper:\n")
    service_end = compose.index("\n  paper-autolearning-scheduler:\n", service_start)
    service = compose[service_start:service_end].replace(
        "      - scripts/docker_runtime_permissions_bootstrap.py\n",
        "      - scripts/run_trade_event_notifications.py\n",
        1,
    )
    compose = compose[:service_start] + service + compose[service_end:]
    root = copy_audit_inputs(tmp_path, compose_text=compose)

    report = audit_notification_runtime_permissions(project_root=root)

    assert report["status"] == "blocked"
    assert report["reason"] == "root_override_without_bind_mount_bootstrap_requirement"
    assert report["runs_as_root"] is True


def test_missing_compose_fails_with_controlled_json_contract(tmp_path: Path) -> None:
    report = audit_notification_runtime_permissions(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert report["reason"] == "docker_compose_paper_missing"
    assert "Traceback" not in json.dumps(report)


def test_audit_is_read_only_by_default(tmp_path: Path) -> None:
    root = copy_audit_inputs(tmp_path)
    report_path = root / "data" / "reports" / "notification_runtime_permissions_audit.json"

    report = audit_notification_runtime_permissions(project_root=root)

    assert report["write_performed"] is False
    assert not report_path.exists()


def test_bootstrap_rejects_root_identity_and_unscoped_paths() -> None:
    command = ["--path", "/app/data/reports", "--", "python", "daemon.py"]
    assert parse_args(command).uid == 10001

    with pytest.raises(SystemExit):
        parse_args(["--uid", "0", *command])
    with pytest.raises(SystemExit):
        parse_args(["--gid", "0", *command])
    with pytest.raises(SystemExit):
        parse_args(["--path", "/app/data", "--", "python", "daemon.py"])


def test_cli_returns_controlled_json_without_docker_or_network() -> None:
    completed = subprocess.run(
        [sys.executable, str(AUDITOR), "--project-root", str(ROOT), "--json"],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert completed.returncode == 0, completed.stderr
    assert payload["status"] == "ok"
    assert payload["write_performed"] is False


def test_auditor_has_no_order_exchange_or_notification_dispatch_calls() -> None:
    tree = ast.parse(AUDITOR.read_text(encoding="utf-8"))
    calls = {
        dotted_name(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    forbidden = {
        "create_order",
        "market_buy",
        "fetch_balance",
        "urllib.request.urlopen",
        "requests.post",
        "NotificationDispatcher",
        "send",
    }

    assert calls.isdisjoint(forbidden)
    source = AUDITOR.read_text(encoding="utf-8").lower()
    assert "ccxt" not in source
    assert "api.telegram.org" not in source
    assert "ntfy.sh" not in source


def test_safety_flags_remain_paper_shadow_only() -> None:
    report = audit_notification_runtime_permissions(project_root=ROOT)

    for name, expected in SAFE_FLAGS.items():
        assert report[name] is expected


def dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""
