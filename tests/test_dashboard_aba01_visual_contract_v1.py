from __future__ import annotations

import ast
import importlib
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PAGE = PROJECT_ROOT / "smartcrypto" / "dashboard" / "pages" / "01_infrastructure.py"
CSS = PROJECT_ROOT / "smartcrypto" / "dashboard" / "assets" / "futuros_command_center.css"
STATUS = PROJECT_ROOT / "smartcrypto" / "dashboard" / "ui" / "status.py"
CARDS = PROJECT_ROOT / "smartcrypto" / "dashboard" / "ui" / "cards.py"
CHARTS = PROJECT_ROOT / "smartcrypto" / "dashboard" / "ui" / "charts.py"
UI_INIT = PROJECT_ROOT / "smartcrypto" / "dashboard" / "ui" / "__init__.py"


FORBIDDEN_STATIC_TERMS = (
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
    "safe_dump",
    "yaml.dump",
    "os.environ",
    "st.secrets",
    "TELEGRAM_BOT_TOKEN",
    "NTFY_TOKEN",
    "api_secret",
    "secret_key",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeUi:
    def __init__(self) -> None:
        self.events: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        def recorder(*args: Any, **kwargs: Any) -> "FakeUi":
            self.events.append((name, args, kwargs))
            return self

        return recorder

    def __enter__(self) -> "FakeUi":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None


def test_aba01_page_exists() -> None:
    assert PAGE.exists()
    assert CSS.exists()


def test_aba01_uses_dashboard_infrastructure_snapshot() -> None:
    text = _read(PAGE)
    assert "data/reports/dashboard_infrastructure_snapshot.json" in text
    assert "DashboardPageId.infrastructure" in text
    assert "load_page_snapshot" in text
    assert "render_snapshot_page" in text


def test_aba01_renders_visual_command_center_contract() -> None:
    text = _read(PAGE)
    required_terms = [
        "Telemetria Institucional",
        "Guardrails permanentes",
        "Latência e conectividade",
        "Runtime evidence",
        "Rate limits",
        "Market data health",
        "Source health matrix",
        "Resumo institucional das demais abas",
        "TABELA CANÔNICA READ-ONLY",
    ]
    for term in required_terms:
        assert term in text


def test_aba01_renders_global_guard_badges() -> None:
    text = _read(PAGE) + _read(CSS)
    for term in [
        "paper",
        "shadow",
        "live_trading_enabled",
        "order_submission_enabled",
        "read-only",
        "BLOCKED",
    ]:
        assert term in text


def test_aba01_css_declares_visual_grid() -> None:
    css = _read(CSS)
    for selector in [
        ".sfc-infra-hero",
        ".sfc-infra-guard-grid",
        ".sfc-aba01-grid",
        ".sfc-telemetry-strip",
        ".sfc-mini-kpi-grid",
        ".sfc-depth-preview",
        ".sfc-grid-preview",
        ".sfc-donut",
    ]:
        assert selector in css


def test_aba01_does_not_import_ccxt_or_runtime_gateways() -> None:
    tree = ast.parse(_read(PAGE))
    imports = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    ]
    assert "ccxt" not in imports
    assert "requests" not in imports
    assert "httpx" not in imports


def test_aba01_static_forbidden_terms_absent() -> None:
    combined = "\n".join(_read(path) for path in [PAGE, STATUS, CARDS, CHARTS, UI_INIT])
    for term in FORBIDDEN_STATIC_TERMS:
        assert term not in combined


def test_aba01_footer_contains_readonly_guards() -> None:
    text = _read(PAGE)
    assert "render_footer_audit_bar" in text
    assert "SNAPSHOT_PATH" in text


def test_aba01_status_severity_order_is_stable() -> None:
    module = _load_module(STATUS, "dashboard_ui_status_contract_v1")
    ordered = [
        "OK",
        "UNKNOWN",
        "STALE",
        "WARNING",
        "ERROR",
        "CRITICAL",
        "BLOCKED",
        "HARD_BLOCKED",
    ]
    ranks = [module.status_severity_rank(status) for status in ordered]
    assert ranks == sorted(ranks)
    assert module.worst_status("OK", "STALE", "BLOCKED") == "blocked"


def test_aba01_missing_snapshot_helper_does_not_crash() -> None:
    module = _load_module(PAGE, "dashboard_aba01_visual_contract_v1")
    ui = FakeUi()
    module.render_missing_snapshot("missing fixture", ui=ui)
    assert any(event[0] == "info" for event in ui.events)


def test_aba01_stale_source_is_not_rendered_as_ok() -> None:
    status_module = _load_module(STATUS, "dashboard_ui_status_contract_v1_stale")
    assert status_module.normalize_status("STALE") == "stale"
    assert status_module.status_to_label("STALE") == "STALE"
    assert status_module.status_severity_rank("STALE") > status_module.status_severity_rank("OK")


def test_aba01_visual_components_escape_html() -> None:
    cards_module = importlib.import_module("smartcrypto.dashboard.ui.cards")
    charts_module = importlib.import_module("smartcrypto.dashboard.ui.charts")
    card_html = cards_module.render_compact_kpi("<script>", "<b>x</b>", status="OK")
    chart_html = charts_module.render_chart_placeholder("<script>", "<b>x</b>", status="OK")
    assert "<script>" not in card_html
    assert "<b>x</b>" not in card_html
    assert "<script>" not in chart_html
    assert "<b>x</b>" not in chart_html
    assert "&lt;script&gt;" in card_html
