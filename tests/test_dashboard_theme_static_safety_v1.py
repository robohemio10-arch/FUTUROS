from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VISUAL_PATHS = [
    ROOT / "smartcrypto" / "dashboard" / "app.py",
    *(ROOT / "smartcrypto" / "dashboard" / "ui").glob("*.py"),
    *(ROOT / "smartcrypto" / "dashboard" / "pages").glob("[0-9][0-9]_*.py"),
]
PROHIBITED_IMPORT_ROOTS = {"ccxt", "requests", "httpx", "aiohttp"}
PROHIBITED_CALLS = {
    "create_order", "cancel_order", "fetch_balance", "fetch_open_orders",
    "execute_command", "dispatch_command", "send_telegram", "send_ntfy",
    "write_text", "write_bytes", "mkdir", "unlink", "rename",
}


def test_visual_layer_has_no_operational_imports_calls_or_writes() -> None:
    for path in VISUAL_PATHS:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name.split(".")[0] for alias in node.names]
                if isinstance(node, ast.ImportFrom) and node.module:
                    names.append(node.module.split(".")[0])
                assert PROHIBITED_IMPORT_ROOTS.isdisjoint(names), path
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                assert name not in PROHIBITED_CALLS, f"{path}:{node.lineno}:{name}"


def test_css_is_local_and_has_no_external_asset_dependency() -> None:
    css = (ROOT / "smartcrypto" / "dashboard" / "assets" / "futuros_command_center.css").read_text(
        encoding="utf-8"
    )
    assert "@import" not in css
    assert "http://" not in css
    assert "https://" not in css
    assert "url(" not in css


def test_new_visual_files_do_not_use_historical_brand_or_ninth_page() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in VISUAL_PATHS)
    assert "BlackRock" not in source
    assert "dashboard_alerts_queue_snapshot.json" not in source
    assert len(list((ROOT / "smartcrypto" / "dashboard" / "pages").glob("[0-9][0-9]_*.py"))) == 8
