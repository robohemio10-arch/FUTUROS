from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

from tests.dashboard_page_test_support import FakeUi, valid_snapshot


ROOT = Path(__file__).resolve().parents[1]
PAGE_PATH = ROOT / "smartcrypto" / "dashboard" / "pages" / "04_opportunity_scanner.py"


def _load_page() -> ModuleType:
    spec = importlib.util.spec_from_file_location("dashboard_aba04_visual_v1", PAGE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rendered_markdown(ui: FakeUi) -> str:
    return "\n".join(
        str(value)
        for name, value in ui.events
        if name == "markdown" and value is not None
    )


def _snapshot_with_materialized_sources(module: ModuleType) -> dict[str, Any]:
    snapshot = valid_snapshot(module.EXPECTED_SCHEMA_VERSION, module.REQUIRED_SECTIONS)
    snapshot.update(
        {
            "dashboard_readonly": True,
            "paper_only": True,
            "shadow_only": True,
            "live_locked": True,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "status_summary": {
                "status": "OK",
                "missing_required_sources": [],
                "missing_optional_sources": [],
                "future_sources_pending": [],
            },
        }
    )
    snapshot["sections"] = {
        "status": {"status": "OK", "opportunity_count": 4},
        "spread_scanner": {
            "status": "OK",
            "opportunities": [
                {
                    "symbol": "BTCUSDT",
                    "exchange_a": "EX_A",
                    "exchange_b": "EX_B",
                    "spread_bps": 7.5,
                    "opportunity_score": 0.82,
                    "projected_net_profit_usdt": 1.25,
                    "status": "OK",
                }
            ],
        },
        "triangular_arbitrage": {
            "status": "OK",
            "opportunities": [
                {
                    "route": "USDT → BTC → ETH → USDT",
                    "triangular_return_pct": 0.18,
                    "opportunity_score": 0.76,
                    "triangular_net_profit_usdt": 0.91,
                    "status": "OK",
                }
            ],
            "real_execution": "HARD_BLOCKED",
        },
        "order_flow_imbalance": {
            "status": "OK",
            "observations": [
                {
                    "symbol": "BTCUSDT",
                    "ofi_score": 0.31,
                    "opportunity_score": 0.55,
                    "status": "OK",
                }
            ],
        },
        "launch_radar": {
            "status": "OK",
            "observations": [
                {"asset": "TOKENX", "score": 0.42, "status": "WATCH"}
            ],
            "sniper_real": "HARD_BLOCKED",
        },
        "opportunity_ranking": {
            "status": "OK",
            "ranking": [
                {
                    "source": "spread",
                    "symbol": "BTCUSDT",
                    "opportunity_score": 0.82,
                    "spread_bps": 7.5,
                    "projected_net_profit_usdt": 1.25,
                    "status": "OK",
                }
            ],
        },
        "events": {
            "status": "OK",
            "events": [
                {
                    "timestamp": "2026-08-12T12:00:00Z",
                    "event_type": "opportunity_observed",
                    "symbol": "BTCUSDT",
                    "message": "fixture",
                    "status": "OK",
                }
            ],
        },
        "governance": {
            "status": "OK",
            "opportunity_scanner": "READ_ONLY",
            "real_arbitrage": "HARD_BLOCKED",
            "sniper_real": "HARD_BLOCKED",
            "multi_exchange_live": "HARD_BLOCKED",
            "dashboard_can_send_order": False,
            "dashboard_can_arm_sniper": False,
        },
        "audit": {"status": "OK", "dashboard_reads_only": True},
    }
    return snapshot


def test_page_preserves_global_readonly_contract_tokens() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")
    assert "load_page_snapshot" in source
    assert "render_snapshot_page" in source
    assert "render_chrome=False" in source
    assert 'if __name__ == "__main__":' in source


def test_page_does_not_reference_backend_generation_or_execution_paths() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")
    forbidden = (
        "builder_registry",
        "allow_writes_to_output_dir",
        "ccxt",
        "create_order",
        "place_order",
        "submit_order",
        "RiskManager",
        "freqtrade_client",
    )
    assert all(token not in source for token in forbidden)


def test_minimal_snapshot_renders_without_external_runtime() -> None:
    module = _load_page()
    ui = FakeUi()
    module.render_page(
        valid_snapshot(module.EXPECTED_SCHEMA_VERSION, module.REQUIRED_SECTIONS),
        ui=ui,
    )
    assert any(name == "title" for name, _ in ui.events)
    assert any(name == "dataframe" for name, _ in ui.events)


def test_unknown_specialized_sources_do_not_become_zero_counts() -> None:
    module = _load_page()
    snapshot = valid_snapshot(module.EXPECTED_SCHEMA_VERSION, module.REQUIRED_SECTIONS)
    for section_name in (
        "status",
        "spread_scanner",
        "triangular_arbitrage",
        "order_flow_imbalance",
        "launch_radar",
        "opportunity_ranking",
    ):
        snapshot["sections"][section_name] = {
            "status": "UNKNOWN",
            "reason": "fixture_not_materialized",
        }
    snapshot["sections"]["governance"] = {
        "status": "OK",
        "opportunity_scanner": "READ_ONLY",
        "real_arbitrage": "HARD_BLOCKED",
        "sniper_real": "HARD_BLOCKED",
        "multi_exchange_live": "HARD_BLOCKED",
        "dashboard_can_send_order": False,
        "dashboard_can_arm_sniper": False,
    }

    ui = FakeUi()
    module.render_page(snapshot, ui=ui)
    html = _rendered_markdown(ui)

    assert "Fontes especializadas não materializadas · UNKNOWN" in html
    assert "Oportunidades observadas" in html
    assert "Spread candidates" in html
    assert "Triangular candidates" in html
    assert ">UNKNOWN<" in html


def test_observed_zero_is_preserved_when_source_is_materialized() -> None:
    module = _load_page()
    snapshot = valid_snapshot(module.EXPECTED_SCHEMA_VERSION, module.REQUIRED_SECTIONS)
    snapshot["sections"]["status"] = {"status": "OK", "opportunity_count": 0}
    snapshot["sections"]["spread_scanner"] = {"status": "OK", "opportunities": []}
    snapshot["sections"]["triangular_arbitrage"] = {"status": "OK", "opportunities": []}
    snapshot["sections"]["order_flow_imbalance"] = {"status": "OK", "observations": []}
    snapshot["sections"]["launch_radar"] = {"status": "OK", "observations": []}
    snapshot["sections"]["opportunity_ranking"] = {"status": "OK", "ranking": []}
    snapshot["sections"]["governance"] = {
        "status": "OK",
        "real_arbitrage": "HARD_BLOCKED",
        "sniper_real": "HARD_BLOCKED",
        "multi_exchange_live": "HARD_BLOCKED",
        "dashboard_can_send_order": False,
        "dashboard_can_arm_sniper": False,
    }

    ui = FakeUi()
    module.render_page(snapshot, ui=ui)
    html = _rendered_markdown(ui)

    assert "Oportunidades observadas" in html
    assert ">0<" in html


def test_materialized_snapshot_renders_ranking_and_specialized_panels() -> None:
    module = _load_page()
    ui = FakeUi()
    module.render_page(_snapshot_with_materialized_sources(module), ui=ui)
    html = _rendered_markdown(ui)

    for expected in (
        "Opportunity Ranking",
        "Scanner de Spread",
        "Arbitragem Triangular",
        "Order Flow Imbalance",
        "Launch Radar",
        "BTCUSDT",
        "USDT → BTC → ETH → USDT",
        "TOKENX",
    ):
        assert expected in html


def test_governance_uses_canonical_sniper_real_and_blocks_execution() -> None:
    module = _load_page()
    ui = FakeUi()
    module.render_page(_snapshot_with_materialized_sources(module), ui=ui)
    html = _rendered_markdown(ui)

    assert "Real Sniper" in html
    assert "HARD_BLOCKED" in html
    assert "Dashboard Can Send Order" in html
    assert "Dashboard Can Arm Sniper" in html
    assert "false" in html
    assert "Execução real bloqueada" in html


def test_page_uses_integer_column_counts_for_fake_ui_compatibility() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")
    assert "columns((1, 1))" not in source
    assert "columns(2)" in source
    assert "columns(3)" in source


def test_primary_kpi_grid_is_balanced_three_by_two_on_desktop() -> None:
    module = _load_page()
    ui = FakeUi()
    module.render_page(_snapshot_with_materialized_sources(module), ui=ui)

    kpi_events = [
        value
        for name, value in ui.events
        if name == "markdown"
        and isinstance(value, str)
        and value.lstrip().startswith('<div class="sfc-mini-kpi ')
    ]

    assert len(kpi_events) == 6

    rendered_kpis = "\n".join(kpi_events)
    for expected_label in (
        "Oportunidades observadas",
        "Ranking materializado",
        "Melhor score",
        "Spread candidates",
        "Triangular candidates",
        "Execução real",
    ):
        assert rendered_kpis.count(expected_label) == 1

    assert all(not value.lstrip().startswith("<style>") for value in kpi_events)


def test_future_sources_are_not_fabricated_in_page_source() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")
    fabricated = (
        "mock_opportunity",
        "fake_spread",
        "sample_arbitrage",
        "synthetic_launch",
        "demo_order_flow",
    )
    assert all(token not in source for token in fabricated)


def test_snapshot_path_and_schema_are_exact() -> None:
    module = _load_page()
    assert module.SNAPSHOT_PATH == "data/reports/dashboard_opportunity_scanner_snapshot.json"
    assert module.EXPECTED_SCHEMA_VERSION == "dashboard_opportunity_scanner_snapshot_v1"
    assert module.REQUIRED_SECTIONS == (
        "status",
        "spread_scanner",
        "triangular_arbitrage",
        "order_flow_imbalance",
        "launch_radar",
        "opportunity_ranking",
        "events",
        "governance",
        "audit",
    )
