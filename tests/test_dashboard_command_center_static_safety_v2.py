from __future__ import annotations

import ast
from pathlib import Path

from smartcrypto.dashboard.security.dashboard_readonly_guard import (
    assert_dashboard_readonly,
    build_readonly_audit_footer,
    get_global_banners,
)
from smartcrypto.ops.dashboard_snapshots.source_catalog import DASHBOARD_SNAPSHOT_FILENAMES


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = ROOT / "smartcrypto" / "dashboard"
FORBIDDEN_IMPORT_ROOTS = {"ccxt", "aiohttp"}
FORBIDDEN_CALL_NAMES = {
    "create_order",
    "cancel_order",
    "fetch_balance",
    "fetch_open_orders",
    "OrderManager",
    "ExchangeGateway",
    "getenv",
}
FORBIDDEN_ATTRIBUTE_CALLS = {
    ("yaml", "dump"),
    ("yaml", "safe_dump"),
    ("requests", "post"),
    ("httpx", "post"),
    ("asyncio", "create_task"),
}
FORBIDDEN_SECRET_NAMES = {"TELEGRAM_TOKEN", "NTFY_TOKEN", "BINANCE_SECRET", "BINANCE_API_KEY"}


def _python_files() -> list[Path]:
    return sorted(DASHBOARD_ROOT.rglob("*.py"))


def _attribute_parts(node: ast.AST) -> tuple[str, ...]:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return tuple(reversed(parts))


def test_dashboard_has_no_operational_imports_or_calls() -> None:
    findings: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in FORBIDDEN_IMPORT_ROOTS:
                        findings.append(f"{path}:{node.lineno}:import:{alias.name}")
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root in FORBIDDEN_IMPORT_ROOTS:
                    findings.append(f"{path}:{node.lineno}:import:{node.module}")
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALL_NAMES:
                    findings.append(f"{path}:{node.lineno}:call:{node.func.id}")
                parts = _attribute_parts(node.func)
                if len(parts) >= 2 and (parts[-2], parts[-1]) in FORBIDDEN_ATTRIBUTE_CALLS:
                    findings.append(f"{path}:{node.lineno}:call:{'.'.join(parts)}")
            elif isinstance(node, ast.Subscript):
                if _attribute_parts(node.value) == ("os", "environ"):
                    findings.append(f"{path}:{node.lineno}:environment_subscript")
            elif isinstance(node, ast.Name) and node.id in FORBIDDEN_SECRET_NAMES:
                findings.append(f"{path}:{node.lineno}:secret_name:{node.id}")

    assert findings == []


def test_dashboard_does_not_use_session_state_as_financial_source() -> None:
    findings: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "session_state":
                findings.append(f"{path}:{node.lineno}")
    assert findings == []


def test_alerts_messaging_snapshot_name_is_canonical() -> None:
    all_text = "\n".join(path.read_text(encoding="utf-8") for path in _python_files())
    assert "dashboard_alerts_queue_snapshot.json" not in all_text
    assert "dashboard_alerts_messaging_snapshot.json" in DASHBOARD_SNAPSHOT_FILENAMES.values()


def test_readonly_guard_exposes_required_banners_and_safe_footer() -> None:
    banners = get_global_banners()
    assert banners == (
        "PAPER / SHADOW ONLY",
        "LIVE LOCKED",
        "ORDER SUBMISSION DISABLED",
        "REAL ORDER SUBMISSION DISABLED",
        "RISKMANAGER AUTHORITY",
        "DASHBOARD READ-ONLY",
    )
    footer = build_readonly_audit_footer()
    assert_dashboard_readonly(footer)
    assert footer["sends_orders"] is False
    assert footer["uses_private_exchange"] is False
