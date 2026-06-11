from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    *sorted((ROOT / "smartcrypto" / "ops" / "dashboard_snapshots").glob("*.py")),
    ROOT / "scripts" / "build_dashboard_snapshots.py",
]
FORBIDDEN_IMPORTS = {"ccxt", "aiohttp", "requests", "httpx"}
FORBIDDEN_CALLS = {
    "create_order",
    "cancel_order",
    "fetch_balance",
    "fetch_open_orders",
    "OrderManager",
    "ExchangeGateway",
    "getenv",
}
FORBIDDEN_ATTRIBUTES = {
    ("yaml", "dump"),
    ("yaml", "safe_dump"),
    ("requests", "post"),
    ("httpx", "post"),
    ("asyncio", "create_task"),
}
SECRET_NAMES = {"TELEGRAM_TOKEN", "NTFY_TOKEN", "BINANCE_SECRET", "BINANCE_API_KEY"}


def attribute_parts(node: ast.AST) -> tuple[str, ...]:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return tuple(reversed(parts))


def test_snapshot_builders_have_no_operational_imports_or_calls() -> None:
    findings: list[str] = []
    for path in TARGETS:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in FORBIDDEN_IMPORTS:
                        findings.append(f"{path.name}:{node.lineno}:import:{alias.name}")
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root in FORBIDDEN_IMPORTS:
                    findings.append(f"{path.name}:{node.lineno}:import:{node.module}")
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
                    findings.append(f"{path.name}:{node.lineno}:call:{node.func.id}")
                parts = attribute_parts(node.func)
                if len(parts) >= 2 and (parts[-2], parts[-1]) in FORBIDDEN_ATTRIBUTES:
                    findings.append(f"{path.name}:{node.lineno}:call:{'.'.join(parts)}")
            elif isinstance(node, ast.Subscript) and attribute_parts(node.value) == ("os", "environ"):
                findings.append(f"{path.name}:{node.lineno}:environment")
            elif isinstance(node, ast.Name) and node.id in SECRET_NAMES:
                findings.append(f"{path.name}:{node.lineno}:secret:{node.id}")
    assert findings == []


def test_alert_snapshot_name_is_canonical() -> None:
    text = "\n".join(path.read_text(encoding="utf-8") for path in TARGETS)
    assert "dashboard_alerts_queue_snapshot.json" not in text
    assert "dashboard_alerts_messaging_snapshot.json" in text
