from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    *(ROOT / "smartcrypto" / "ops" / "dashboard_semantic_audit").glob("*.py"),
    ROOT / "scripts" / "audit_dashboard_semantic_coverage_v2.py",
]
FORBIDDEN_IMPORT_ROOTS = {"ccxt", "requests", "httpx", "aiohttp"}
FORBIDDEN_CALLS = {
    "create_order", "cancel_order", "fetch_balance", "fetch_open_orders",
    "send_telegram", "send_ntfy", "write_text", "write_bytes", "mkdir", "unlink",
    "rename",
}
FORBIDDEN_TEXT = (
    "dashboard_alerts_" + "queue_snapshot.json",
    "TELEGRAM_TOKEN",
    "NTFY_TOKEN",
    "BINANCE_SECRET",
    "BINANCE_API_KEY",
)


def test_semantic_audit_layer_has_no_operational_imports_calls_or_writes() -> None:
    for path in TARGETS:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] not in FORBIDDEN_IMPORT_ROOTS for alias in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in FORBIDDEN_IMPORT_ROOTS
            if isinstance(node, ast.Call):
                name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                assert name not in FORBIDDEN_CALLS, f"{path}:{node.lineno}:{name}"


def test_semantic_audit_layer_does_not_embed_secret_or_deprecated_snapshot_terms() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in TARGETS)
    for token in FORBIDDEN_TEXT:
        assert token not in combined
