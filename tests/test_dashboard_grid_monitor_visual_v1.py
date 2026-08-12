from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
PAGE_PATH = ROOT / "smartcrypto" / "dashboard" / "pages" / "03_grid_monitor.py"


class _FakeSurface:
    def __init__(self, sink: list[str]) -> None:
        self._sink = sink

    def markdown(self, body: str, *, unsafe_allow_html: bool = False) -> None:
        del unsafe_allow_html
        self._sink.append(str(body))


class _FakeUI(_FakeSurface):
    def columns(self, spec: Any) -> tuple[_FakeSurface, ...]:
        count = spec if isinstance(spec, int) else len(spec)
        return tuple(_FakeSurface(self._sink) for _ in range(count))

    def info(self, body: str) -> None:
        self._sink.append(str(body))


@pytest.fixture(scope="module")
def page() -> ModuleType:
    spec = importlib.util.spec_from_file_location("dashboard_grid_monitor_visual_v1", PAGE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def snapshot() -> dict[str, Any]:
    return {
        "schema_version": "dashboard_grid_monitor_snapshot_v1",
        "runtime_mode": "paper",
        "dashboard_readonly": True,
        "paper_only": True,
        "shadow_only": True,
        "live_locked": True,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "last_updated_utc": "2026-08-12T10:00:00Z",
        "status_summary": {
            "status": "OK",
            "missing_required_sources_count": 0,
            "missing_optional_sources_count": 0,
            "future_sources_pending_count": 1,
            "errors_count": 0,
        },
        "sections": {
            "selected_grid": {
                "status": "OK",
                "reason": "ok",
                "symbol": "BTCUSDT",
                "current_price": 65000.0,
            },
            "grid_channel": {
                "status": "OK",
                "reason": "ok",
                "lower_price": 64000.0,
                "upper_price": 66000.0,
                "current_price": 65000.0,
                "level_prices": [64000.0, 65000.0, 66000.0],
                "grid_center": 65000.0,
                "metrics_available": True,
            },
            "grid_density": {
                "status": "OK",
                "reason": "ok",
                "expected_levels": 3,
                "active_levels": 3,
                "missing_levels": 0,
            },
            "dust": {
                "status": "OK",
                "reason": "ok",
                "dust_qty": 0.0,
                "dust_value_usdt": 0.0,
                "dust_portfolio_pct": 0.0,
            },
            "order_book": {
                "status": "OK",
                "reason": "ok",
                "best_bid": 64999.0,
                "best_ask": 65001.0,
                "spread_bps": 0.3076923077,
                "top_of_book_depth_usdt": 162497.5,
                "bid_depth_usdt": 64999.0,
                "ask_depth_usdt": 97501.5,
                "order_book_imbalance": -0.2,
                "bids": [
                    {
                        "price": 64999.0,
                        "quantity": 1.0,
                        "notional_usdt": 64999.0,
                        "cumulative_quantity": 1.0,
                        "cumulative_notional_usdt": 64999.0,
                    }
                ],
                "asks": [
                    {
                        "price": 65001.0,
                        "quantity": 1.5,
                        "notional_usdt": 97501.5,
                        "cumulative_quantity": 1.5,
                        "cumulative_notional_usdt": 97501.5,
                    }
                ],
                "depth_levels_truncated": False,
                "depth_materialized": True,
            },
            "heatmap": {
                "status": "UNKNOWN",
                "reason": "ok",
                "levels": [64000.0, 65000.0, 66000.0],
                "heatmap_available": False,
                "heatmap_reason": "time_range_heatmap_source_not_materialized",
                "level_distribution": [
                    {
                        "bucket_index": 0,
                        "lower_price": 64000.0,
                        "upper_price": 65000.0,
                        "level_count": 2,
                        "level_share_pct": 66.6666666667,
                    },
                    {
                        "bucket_index": 1,
                        "lower_price": 65000.0,
                        "upper_price": 66000.0,
                        "level_count": 1,
                        "level_share_pct": 33.3333333333,
                    },
                ],
                "level_distribution_kind": "grid_level_price_histogram",
                "level_distribution_available": True,
            },
            "last_executions": {"status": "OK", "reason": "ok", "executions": []},
            "grid_summary": {"status": "OK", "reason": "ok", "mode": "NORMAL"},
            "integrity": {
                "status": "OK",
                "reason": "ok",
                "grid_integrity_score": 100.0,
                "duplicate_orders": 0,
                "gap_count": 0,
                "outside_channel_count": 0,
                "stale_data": False,
                "kill_switch_active": False,
            },
            "audit": {
                "status": "OK",
                "reason": "ok",
                "dashboard_reads_only": True,
                "snapshot_contract_hardened": True,
                "order_book_depth_materialized": True,
                "grid_level_distribution_available": True,
                "heatmap_available": False,
            },
        },
        "safety": {
            "paper_only": True,
            "shadow_only": True,
            "sends_orders": False,
            "changes_risk": False,
            "changes_model": False,
            "changes_active_signals": False,
            "uses_private_exchange": False,
            "uses_ccxt": False,
        },
        "audit": {"dashboard_reads_only": True},
    }


def _disable_chrome(monkeypatch: pytest.MonkeyPatch, page: ModuleType) -> None:
    for name in (
        "inject_smart_futuros_command_center_css",
        "render_global_topbar",
        "render_sidebar",
        "render_page_title",
        "render_readonly_banner",
        "render_footer_audit_bar",
    ):
        monkeypatch.setattr(page, name, lambda *args, **kwargs: None)


def test_render_passes_exact_snapshot_data_to_data_driven_visuals(
    monkeypatch: pytest.MonkeyPatch,
    page: ModuleType,
    snapshot: dict[str, Any],
) -> None:
    _disable_chrome(monkeypatch, page)
    captured: dict[str, Any] = {}

    def capture_channel(**kwargs: Any) -> str:
        captured["channel"] = kwargs
        return "<channel/>"

    def capture_depth(**kwargs: Any) -> str:
        captured["depth"] = kwargs
        return "<depth/>"

    def capture_distribution(values: dict[str, Any], **kwargs: Any) -> str:
        captured["distribution"] = {"values": values, **kwargs}
        return "<distribution/>"

    monkeypatch.setattr(page, "render_grid_channel_preview", capture_channel)
    monkeypatch.setattr(page, "render_depth_preview", capture_depth)
    monkeypatch.setattr(page, "render_mini_bar_stack", capture_distribution)

    sink: list[str] = []
    page.render_page(snapshot, ui=_FakeUI(sink))

    assert captured["channel"]["lower_price"] == 64000.0
    assert captured["channel"]["upper_price"] == 66000.0
    assert captured["channel"]["current_price"] == 65000.0
    assert captured["channel"]["level_prices"] == [64000.0, 65000.0, 66000.0]
    assert captured["channel"]["status"] == "OK"

    assert captured["depth"]["bids"] == snapshot["sections"]["order_book"]["bids"]
    assert captured["depth"]["asks"] == snapshot["sections"]["order_book"]["asks"]
    assert captured["depth"]["status"] == "OK"

    assert captured["distribution"]["values"] == {"B00": 2.0, "B01": 1.0}
    assert captured["distribution"]["label"] == "Densidade espacial por bucket"


def test_heatmap_remains_explicit_unknown_without_temporal_axis(
    monkeypatch: pytest.MonkeyPatch,
    page: ModuleType,
    snapshot: dict[str, Any],
) -> None:
    _disable_chrome(monkeypatch, page)
    placeholders: list[dict[str, Any]] = []

    def capture_placeholder(title: str, message: str, status: str = "unknown") -> str:
        placeholders.append({"title": title, "message": message, "status": status})
        return "<placeholder/>"

    monkeypatch.setattr(page, "render_chart_placeholder", capture_placeholder)

    sink: list[str] = []
    page.render_page(snapshot, ui=_FakeUI(sink))

    heatmap_calls = [item for item in placeholders if item["title"] == "Heatmap temporal"]
    assert len(heatmap_calls) == 1
    heatmap = heatmap_calls[0]
    assert heatmap["status"] == "UNKNOWN"
    assert "time_range_heatmap_source_not_materialized" in heatmap["message"]
    assert "UNKNOWN" in heatmap["message"]


def test_missing_visual_structures_are_not_backfilled_with_synthetic_data(
    monkeypatch: pytest.MonkeyPatch,
    page: ModuleType,
    snapshot: dict[str, Any],
) -> None:
    _disable_chrome(monkeypatch, page)
    snapshot["sections"]["grid_channel"] = {
        "status": "UNKNOWN",
        "reason": "missing",
        "lower_price": None,
        "upper_price": None,
        "current_price": None,
        "level_prices": [],
    }
    snapshot["sections"]["order_book"] = {
        "status": "UNKNOWN",
        "reason": "missing",
        "bids": [],
        "asks": [],
        "depth_materialized": False,
    }
    snapshot["sections"]["heatmap"] = {
        "status": "UNKNOWN",
        "reason": "missing",
        "heatmap_available": False,
        "heatmap_reason": "grid_level_distribution_unavailable",
        "level_distribution_available": False,
        "level_distribution": [],
    }

    captured: dict[str, Any] = {}

    def capture_channel(**kwargs: Any) -> str:
        captured["channel"] = kwargs
        return "<channel/>"

    def capture_depth(**kwargs: Any) -> str:
        captured["depth"] = kwargs
        return "<depth/>"

    monkeypatch.setattr(page, "render_grid_channel_preview", capture_channel)
    monkeypatch.setattr(page, "render_depth_preview", capture_depth)

    sink: list[str] = []
    page.render_page(snapshot, ui=_FakeUI(sink))

    assert captured["channel"]["lower_price"] is None
    assert captured["channel"]["upper_price"] is None
    assert captured["channel"]["level_prices"] == []
    assert captured["channel"]["status"] == "UNKNOWN"
    assert captured["depth"]["bids"] == []
    assert captured["depth"]["asks"] == []
    assert captured["depth"]["status"] == "UNKNOWN"
    assert page._distribution_bar_values(snapshot["sections"]["heatmap"]) == {}
    assert any("Buckets de níveis não materializados" in html for html in sink)


def test_stale_and_blocked_statuses_are_preserved_by_visual_calls(
    monkeypatch: pytest.MonkeyPatch,
    page: ModuleType,
    snapshot: dict[str, Any],
) -> None:
    _disable_chrome(monkeypatch, page)
    snapshot["sections"]["grid_channel"]["status"] = "STALE"
    snapshot["sections"]["integrity"]["status"] = "BLOCKED"
    snapshot["sections"]["integrity"]["reason"] = "kill_switch_or_stale"

    captured: dict[str, Any] = {}

    def capture_channel(**kwargs: Any) -> str:
        captured["channel_status"] = kwargs["status"]
        return "<channel/>"

    def capture_status_card(title: str, status: str, **kwargs: Any) -> str:
        captured["integrity_status"] = status
        captured["integrity_title"] = title
        return "<status/>"

    monkeypatch.setattr(page, "render_grid_channel_preview", capture_channel)
    monkeypatch.setattr(page, "render_status_card", capture_status_card)

    sink: list[str] = []
    page.render_page(snapshot, ui=_FakeUI(sink))

    assert captured["channel_status"] == "STALE"
    assert captured["integrity_status"] == "BLOCKED"
    assert captured["integrity_title"] == "Estado de integridade"


def test_zero_observed_values_are_not_converted_to_unknown(
    page: ModuleType,
    snapshot: dict[str, Any],
) -> None:
    assert page._format_count(0) == "0"
    assert page._format_usdt(0) == "US$ 0.00"
    assert page._format_decimal(0, digits=3) == "0.000"
    assert page._format_bool(False) == "false"

    rows = page._integrity_rows(
        snapshot["sections"]["integrity"],
        snapshot["sections"]["grid_density"],
        snapshot["sections"]["dust"],
    )
    values = {row["Métrica"]: row["Valor"] for row in rows}
    assert values["Ordens duplicadas"] == "0"
    assert values["Níveis ausentes"] == "0"
    assert values["Dust"] == "US$ 0.00"


def test_visual_semantics_remove_legacy_wrong_label_and_generic_placeholder() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")
    assert "Grids Vendidos" not in source
    assert "Níveis ausentes" in source
    assert "render_snapshot_page" not in source
    assert "Snapshot sem série visual suficiente" not in source
    assert "Histograma espacial de níveis por faixa de preço" in source
    assert "Não representa um heatmap temporal de mercado" in source


def test_page_is_read_only_and_has_no_operational_or_private_exchange_authority() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    forbidden_import_prefixes = (
        "ccxt",
        "freqtrade",
        "qlib",
        "smartcrypto.risk",
        "smartcrypto.execution",
        "smartcrypto.exchange",
        "smartcrypto.trading",
    )
    forbidden_calls = {
        "create_order",
        "submit_order",
        "cancel_order",
        "place_order",
        "write_text",
        "write_bytes",
        "unlink",
        "rename",
        "replace",
        "mkdir",
    }

    imported_modules: list[str] = []
    called_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.append(node.module or "")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called_names.append(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called_names.append(node.func.attr)

    assert not any(
        module == prefix or module.startswith(prefix + ".")
        for module in imported_modules
        for prefix in forbidden_import_prefixes
    )
    assert not forbidden_calls.intersection(called_names)


def test_safety_diagnostics_preserve_paper_shadow_and_denied_actions(
    page: ModuleType,
    snapshot: dict[str, Any],
) -> None:
    rows = page._safety_rows(snapshot)
    values = {row["Controle"]: row["Valor"] for row in rows}

    assert values["dashboard_readonly"] == "true"
    assert values["paper_only"] == "true"
    assert values["shadow_only"] == "true"
    assert values["live_locked"] == "true"
    assert values["order_submission_enabled"] == "false"
    assert values["real_order_submission_enabled"] == "false"
    assert values["sends_orders"] == "false"
    assert values["changes_risk"] == "false"
    assert values["changes_model"] == "false"
    assert values["uses_private_exchange"] == "false"
