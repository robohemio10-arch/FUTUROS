from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    ROOT / "smartcrypto" / "dashboard" / "controls",
    ROOT / "smartcrypto" / "dashboard" / "alerts",
    ROOT / "smartcrypto" / "dashboard" / "components" / "control_stubs.py",
    ROOT / "smartcrypto" / "dashboard" / "components" / "alert_stubs.py",
    ROOT / "smartcrypto" / "dashboard" / "components" / "readiness_gates.py",
    ROOT / "smartcrypto" / "dashboard" / "components" / "decision_trace.py",
    ROOT / "smartcrypto" / "dashboard" / "components" / "dataset_pipeline.py",
    ROOT / "smartcrypto" / "dashboard" / "pages" / "06_active_controls.py",
    ROOT / "smartcrypto" / "dashboard" / "pages" / "07_quantitative_reports.py",
    ROOT / "smartcrypto" / "dashboard" / "pages" / "08_alerts_messaging.py",
)

FORBIDDEN_IMPORT_ROOTS = {"ccxt", "requests", "httpx", "aiohttp", "subprocess"}
FORBIDDEN_CALLS = {
    "create_order", "cancel_order", "fetch_balance", "fetch_open_orders", "post",
    "write_text", "write_bytes", "mkdir", "unlink", "rename", "replace", "open",
}


def source_paths() -> list[Path]:
    paths: list[Path] = []
    for target in TARGETS:
        paths.extend(target.rglob("*.py") if target.is_dir() else [target])
    return sorted(set(paths))


def test_new_stub_layer_has_no_operational_imports_or_calls() -> None:
    for path in source_paths():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] not in FORBIDDEN_IMPORT_ROOTS for alias in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in FORBIDDEN_IMPORT_ROOTS
            if isinstance(node, ast.Call):
                name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                assert name not in FORBIDDEN_CALLS, f"{path}:{node.lineno}:{name}"


def test_canonical_alert_snapshot_and_no_ninth_page() -> None:
    text = "\n".join(path.read_text(encoding="utf-8") for path in source_paths())
    assert "dashboard_alerts_messaging_snapshot.json" in text
    assert "dashboard_alerts_queue_snapshot.json" not in text
    pages = list((ROOT / "smartcrypto" / "dashboard" / "pages").glob("[0-9][0-9]_*.py"))
    assert len(pages) == 8
    assert "BlackRock" not in text


def test_n4_commands_are_policy_hard_blocks() -> None:
    from smartcrypto.dashboard.controls.command_classifier import classify_dashboard_command
    from smartcrypto.dashboard.controls.policies import N4_COMMANDS

    assert N4_COMMANDS
    assert all(classify_dashboard_command(command).hard_blocked for command in N4_COMMANDS)
