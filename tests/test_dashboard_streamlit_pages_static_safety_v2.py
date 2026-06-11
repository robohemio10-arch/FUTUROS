from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = ROOT / "smartcrypto" / "dashboard"

PROHIBITED_TEXT = (
    "import ccxt",
    "ccxt.",
    "create_order(",
    "cancel_order(",
    "fetch_balance(",
    "fetch_open_orders(",
    "OrderManager(",
    "ExchangeGateway(",
    "yaml.dump(",
    "yaml.safe_dump(",
    "requests.post(",
    "httpx.post(",
    "aiohttp.",
    "asyncio.create_task(",
    "os.environ[",
    "getenv(",
    "TELEGRAM_TOKEN",
    "NTFY_TOKEN",
    "BINANCE_SECRET",
    "BINANCE_API_KEY",
    "dashboard_alerts_queue_snapshot.json",
)
BACKEND_GENERATION_NAMES = (
    "build_dashboard_snapshots",
    "build_infrastructure_snapshot",
    "build_portfolio_risk_snapshot",
    "build_grid_monitor_snapshot",
    "build_opportunity_scanner_snapshot",
    "build_ai_governance_snapshot",
    "build_active_controls_snapshot",
    "build_quantitative_reports_snapshot",
    "build_alerts_messaging_snapshot",
)
PROHIBITED_CALL_ATTRIBUTES = {
    "write_text", "write_bytes", "mkdir", "unlink", "rename",
}


def test_dashboard_python_has_no_operational_calls_or_secret_access() -> None:
    for path in DASHBOARD_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in PROHIBITED_TEXT:
            assert token not in text, f"{path}:{token}"
        for name in BACKEND_GENERATION_NAMES:
            assert name not in text, f"{path}:{name}"


def test_dashboard_python_has_no_filesystem_write_or_process_calls() -> None:
    for path in DASHBOARD_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute):
                assert node.func.attr not in PROHIBITED_CALL_ATTRIBUTES, (
                    f"{path}:{node.lineno}:{node.func.attr}"
                )
                if isinstance(node.func.value, ast.Name) and node.func.value.id in {"subprocess", "os"}:
                    assert node.func.attr not in {"run", "Popen", "system"}


def test_dashboard_safety_language_is_explicit() -> None:
    text = (DASHBOARD_ROOT / "components" / "read_only.py").read_text(encoding="utf-8")
    for phrase in (
        "PAPER / SHADOW ONLY", "LIVE LOCKED", "ORDER SUBMISSION DISABLED",
        "REAL ORDER SUBMISSION DISABLED", "RISKMANAGER AUTHORITY", "DASHBOARD READ-ONLY",
    ):
        assert phrase in text or phrase in (
            DASHBOARD_ROOT / "security" / "dashboard_readonly_guard.py"
        ).read_text(encoding="utf-8")
