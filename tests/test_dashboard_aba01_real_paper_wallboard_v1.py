from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PAGE = PROJECT_ROOT / "smartcrypto" / "dashboard" / "pages" / "01_infrastructure.py"

FORBIDDEN_TEXT = (
    "import ccxt",
    "create_order",
    "cancel_order",
    "fetch_balance",
    "fetch_open_orders",
    "OrderManager",
    "ExchangeGateway",
    "requests.post",
    "httpx.post",
    "aiohttp",
)

REQUIRED_TEXT = (
    "REAL_PAPER_SNAPSHOT_PATH",
    "dashboard_real_paper_sources_snapshot.json",
    "dashboard_infrastructure_snapshot.json",
    "Paper real · execução observada",
    "Freqtrade paper real",
    "Trades",
    "Ordens",
    "PnL Realizado",
    "Qlib e sinais ativos",
    "Mensageria observada",
    "Safety do snapshot real paper",
    "dashboard_readonly",
    "paper_only",
    "shadow_only",
    "live_trading_enabled",
    "order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "sends_notifications",
)


def _source() -> str:
    return PAGE.read_text(encoding="utf-8")


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("aba01_real_paper_wallboard", PAGE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_aba01_references_real_paper_snapshot_without_replacing_infrastructure_snapshot() -> None:
    source = _source()

    assert 'SNAPSHOT_PATH = "data/reports/dashboard_infrastructure_snapshot.json"' in source
    assert 'REAL_PAPER_SNAPSHOT_PATH = "data/reports/dashboard_real_paper_sources_snapshot.json"' in source
    assert "_load_real_paper_snapshot()" in source
    assert "_real_paper_wallboard_html(real_paper_snapshot)" in source


def test_aba01_real_paper_wallboard_contract_text_is_present() -> None:
    source = _source()

    for expected in REQUIRED_TEXT:
        assert expected in source


def test_aba01_real_paper_wallboard_has_no_forbidden_runtime_authority() -> None:
    source = _source()

    for forbidden in FORBIDDEN_TEXT:
        assert forbidden not in source


def test_aba01_real_paper_wallboard_imports_do_not_include_runtime_gateways() -> None:
    tree = ast.parse(_source())
    imports: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)

    assert "ccxt" not in imports
    assert "requests" not in imports
    assert "httpx" not in imports
    assert "aiohttp" not in imports


def test_load_real_paper_snapshot_missing_is_safe() -> None:
    module = _load_module()

    payload = module._load_real_paper_snapshot("data/reports/does_not_exist_real_paper_snapshot.json")

    assert payload["status"] == "MISSING"
    assert payload["reason"] == "real_paper_snapshot_not_found"
    assert payload["safety"]["dashboard_readonly"] is True
    assert payload["safety"]["paper_only"] is True
    assert payload["safety"]["shadow_only"] is True
    assert payload["safety"]["live_trading_enabled"] is False
    assert payload["safety"]["order_submission_enabled"] is False
    assert payload["safety"]["exchange_private_access"] is False
    assert payload["safety"]["sends_orders"] is False
    assert payload["safety"]["sends_notifications"] is False


def test_real_paper_wallboard_renders_real_values_and_safety_flags() -> None:
    module = _load_module()

    html = module._real_paper_wallboard_html(
        {
            "status": "ok",
            "reason": "real_paper_sources_available",
            "freqtrade": {
                "trades_total": 353,
                "closed_trades": 352,
                "open_trades": 1,
                "orders_total": 732,
                "realized_pnl_abs": -46.82265349,
                "win_rate": 51.2,
                "open_exposure_usdt": 100.25,
                "fees_total": 3.14,
            },
            "qlib": {
                "status": "ok",
                "model_version": "qlib_lgbm_v1",
                "prediction_rows": 2,
                "signals_count": 2,
                "input_data_status": "input_data_fresh",
            },
            "alerts": {
                "events_total": 707,
                "channels_total": 218,
                "pending_total": 0,
            },
            "safety": {
                "dashboard_readonly": True,
                "paper_only": True,
                "shadow_only": True,
                "live_trading_enabled": False,
                "order_submission_enabled": False,
                "real_order_submission_enabled": False,
                "exchange_private_access": False,
                "sends_orders": False,
                "sends_notifications": False,
            },
        }
    )

    assert "Paper real · execução observada" in html
    assert "Freqtrade paper real" in html
    assert "Trades" in html
    assert "353" in html
    assert "Ordens" in html
    assert "732" in html
    assert "PnL Realizado" in html
    assert "-46.82" in html
    assert "qlib_lgbm_v1" in html
    assert "Eventos" in html
    assert "707" in html
    assert "dashboard_readonly" in html
    assert "live_trading_enabled" in html
    assert "False" in html


def test_real_paper_safety_guard_rejects_unsafe_flags() -> None:
    module = _load_module()

    assert module._real_paper_safety_ok(
        {
            "dashboard_readonly": True,
            "paper_only": True,
            "shadow_only": True,
            "live_trading_enabled": False,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "exchange_private_access": False,
            "sends_orders": False,
            "sends_notifications": False,
        }
    )

    assert not module._real_paper_safety_ok(
        {
            "dashboard_readonly": True,
            "paper_only": True,
            "shadow_only": True,
            "live_trading_enabled": True,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "exchange_private_access": False,
            "sends_orders": False,
            "sends_notifications": False,
        }
    )
