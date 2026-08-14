from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from smartcrypto.ops.dashboard_snapshots.contracts import DashboardAuditContract
from tests.dashboard_page_test_support import FakeUi, load_page_module, valid_snapshot


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "smartcrypto" / "dashboard" / "pages" / "05_ai_governance.py"


def _load_page() -> Any:
    return load_page_module(PAGE)


def _snapshot(module: Any) -> dict[str, Any]:
    snapshot = valid_snapshot(
        module.EXPECTED_SCHEMA_VERSION,
        module.REQUIRED_SECTIONS,
    )
    snapshot["status_summary"] = {"status": "WARNING"}
    snapshot["sections"]["model_state"] = {
        "status": "OK",
        "registry": {"model_name": "qlib-shadow-challenger-v1"},
        "active_model": {},
    }
    snapshot["sections"]["qlib_ranking"] = {
        "status": "OK",
        "ranking": [
            {
                "symbol": "BTC/USDT",
                "expected_trade_value": 0.012345,
                "expected_return_net": 0.018,
                "probability": 0.61,
                "status": "OK",
            }
        ],
    }
    snapshot["sections"]["shadow_veto"] = {
        "status": "OK",
        "ai_accept_count": 8,
        "ai_reject_count": 2,
        "ai_accept_rate_pct": 80.0,
        "ai_reject_rate_pct": 20.0,
    }
    snapshot["sections"]["decision_governance"] = {
        "status": "OK",
        "final_action": "NO_TRADE",
        "riskmanager_authority": True,
        "ai_can_increase_risk": False,
    }
    snapshot["sections"]["drift_regime"] = {
        "status": "WARNING",
        "psi": 0.15,
        "drift_status": "WARNING",
    }
    snapshot["sections"]["shadow_classification_metrics"] = {
        "status": "OK",
        "precision": 0.8,
        "recall": 0.7,
        "f1_score": 0.7467,
        "accuracy": 0.82,
        "brier_score": 0.12,
    }
    snapshot["sections"]["reward_research"] = {
        "status": "UNKNOWN",
        "research_only": True,
    }
    snapshot["sections"]["model_governance"] = {
        "status": "OK",
        "auto_promotion_allowed": False,
        "live_model_promotion_allowed": False,
        "model_promotion_allowed_from_dashboard": False,
        "accuracy_is_primary_metric": False,
        "promotion_status": "HARD_BLOCKED",
    }
    snapshot["sections"]["ai_training_research_command_center"] = {
        "status": "WARNING",
        "research_gate_status": "BLOCKED",
        "decision": "MANTER_EM_RESEARCH",
        "authority": "advisory_only",
        "operational_authority": False,
        "summary": {
            "source_count": 8,
            "available_source_count": 8,
            "missing_optional_source_count": 0,
        },
        "branch_cards": [
            {
                "branch_id": f"branch{index:02d}",
                "title": f"Research branch {index:02d}",
                "status": "WARNING",
                "decision": "MANTER_EM_RESEARCH",
                "headline_metric": {"label": "Metric", "value": index},
                "advisory_only": True,
            }
            for index in range(1, 9)
        ],
        "blockers": ["research_candidate_not_promotable"],
        "missing_optional_sources": [],
        "safety_flags": {
            "paper_only": True,
            "shadow_only": True,
            "operational_authority": False,
            "updates_freqtrade": False,
            "updates_qlib_runtime": False,
            "updates_risk_manager": False,
            "updates_ai_shadow_runtime": False,
            "sends_orders": False,
            "changes_risk": False,
            "changes_model": False,
            "registers_model": False,
            "production_enabled": False,
            "live_trading_enabled": False,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "exchange_private_access": False,
        },
    }
    snapshot["sections"]["audit"] = {
        "status": "OK",
        "dashboard_reads_only": True,
        "trains_model": False,
        "promotes_model": False,
    }
    snapshot["audit"] = DashboardAuditContract(
        snapshot_source="test_dashboard_ai_governance_visual_v1"
    ).to_dict()
    return snapshot


def _markdown_values(ui: FakeUi) -> list[str]:
    return [
        value
        for name, value in ui.events
        if name == "markdown" and isinstance(value, str)
    ]


def _rendered_html(ui: FakeUi) -> str:
    return "\n".join(_markdown_values(ui))


def test_page05_visual_contract_and_snapshot_path_are_stable() -> None:
    module = _load_page()
    assert module.PAGE_NUMBER == "05"
    assert module.PAGE_TITLE == "05. IA / Qlib Governance"
    assert module.SNAPSHOT_PATH == "data/reports/dashboard_ai_governance_snapshot.json"
    assert module.EXPECTED_SCHEMA_VERSION == "dashboard_ai_governance_snapshot_v1"
    assert "ai_training_research_command_center" in module.REQUIRED_SECTIONS


def test_primary_kpi_grid_is_balanced_three_by_two() -> None:
    module = _load_page()
    ui = FakeUi()
    module.render_page(_snapshot(module), ui=ui)

    kpi_events = [
        value
        for value in _markdown_values(ui)
        if value.lstrip().startswith('<div class="sfc-mini-kpi ')
    ]
    assert len(kpi_events) == 6

    rendered = "\n".join(kpi_events)
    for label in (
        "Model / Registry",
        "IA Shadow Decisions",
        "Drift / PSI",
        "Research Gate",
        "Model Promotion",
        "RiskManager Authority",
    ):
        assert rendered.count(label) == 1


def test_kpis_use_authoritative_builder_fields_not_legacy_metric_paths() -> None:
    module = _load_page()
    ui = FakeUi()
    module.render_page(_snapshot(module), ui=ui)
    rendered = _rendered_html(ui)

    assert "A:8 / R:2" in rendered
    assert "0.1500" in rendered
    assert "BLOCKED" in rendered
    assert "HARD-BLOCKED" in rendered
    assert "qlib_status" not in PAGE.read_text(encoding="utf-8")
    assert "ai_shadow_status" not in PAGE.read_text(encoding="utf-8")


def test_qlib_ranking_renders_only_materialized_rows() -> None:
    module = _load_page()
    snapshot = _snapshot(module)
    ui = FakeUi()
    module.render_page(snapshot, ui=ui)
    rendered = _rendered_html(ui)

    assert "BTC/USDT" in rendered
    assert "0.012345" in rendered

    snapshot["sections"]["qlib_ranking"] = {"status": "UNKNOWN"}
    ui_missing = FakeUi()
    module.render_page(snapshot, ui=ui_missing)
    missing_rendered = _rendered_html(ui_missing)

    assert "Ranking Qlib não materializado no snapshot canônico · UNKNOWN" in missing_rendered
    assert "BTC/USDT" not in missing_rendered


def test_reward_research_does_not_fabricate_expected_trade_value() -> None:
    module = _load_page()
    snapshot = _snapshot(module)
    reward = snapshot["sections"]["reward_research"]
    assert "expected_trade_value" not in reward

    ui = FakeUi()
    module.render_page(snapshot, ui=ui)
    rendered = _rendered_html(ui)

    assert "Research only" in rendered
    assert "Expected Trade Value" not in rendered


def test_decision_and_model_governance_are_fail_closed() -> None:
    module = _load_page()
    snapshot = _snapshot(module)
    ui = FakeUi()
    module.render_page(snapshot, ui=ui)
    rendered = _rendered_html(ui)

    assert "NO_TRADE" in rendered
    assert "RiskManager authority" in rendered
    assert "AI can increase risk" in rendered
    assert "Promotion status" in rendered
    assert rendered.count("HARD-BLOCKED") >= 1

    rows = module._model_governance_rows(snapshot["sections"]["model_governance"])
    values = {row["Controle"]: row["Valor"] for row in rows}
    assert values["Promotion status"] == "HARD_BLOCKED"
    assert values["Auto promotion allowed"] == "false"
    assert values["Live model promotion allowed"] == "false"
    assert values["Model promotion allowed from dashboard"] == "false"
    assert values["Accuracy is primary metric"] == "false"


def test_model_governance_boolean_values_are_not_relabelled_as_ok() -> None:
    module = _load_page()
    ui = FakeUi()
    module.render_page(_snapshot(module), ui=ui)
    rendered = _rendered_html(ui)

    assert "Auto promotion allowed</td><td>false</td>" in rendered
    assert "Live model promotion allowed</td><td>false</td>" in rendered
    assert "Model promotion allowed from dashboard</td><td>false</td>" in rendered

    source = PAGE.read_text(encoding="utf-8")
    assert 'status_columns=["Valor"]' not in source
    assert 'return "OK" if value is False else "BLOCKED"' not in source


def test_research_command_center_renders_eight_advisory_branch_cards() -> None:
    module = _load_page()
    ui = FakeUi()
    module.render_page(_snapshot(module), ui=ui)
    rendered = _rendered_html(ui)

    assert "8/8" in rendered
    assert "advisory_only" in rendered
    assert "research_candidate_not_promotable" in rendered
    for index in range(1, 9):
        assert f"branch{index:02d}" in rendered


def test_research_missing_optional_is_not_rendered_as_zero_coverage() -> None:
    module = _load_page()
    snapshot = _snapshot(module)
    snapshot["sections"]["ai_training_research_command_center"] = {
        "status": "MISSING_OPTIONAL",
        "research_gate_status": "BLOCKED",
        "decision": "MANTER_EM_RESEARCH",
        "authority": "advisory_only",
        "branch_cards": [],
        "missing_optional_sources": ["optional_research.json"],
        "safety_flags": {},
    }
    ui = FakeUi()
    module.render_page(snapshot, ui=ui)
    rendered = _rendered_html(ui)

    assert "Research branch cards não materializados · MISSING_OPTIONAL" in rendered
    assert "optional_research.json" in rendered
    assert ">0/8<" not in rendered
    assert ">0<" not in rendered


def test_canonical_snapshot_and_existing_research_component_are_preserved() -> None:
    module = _load_page()
    source = PAGE.read_text(encoding="utf-8")

    assert "render_snapshot_page(" in source
    assert "render_chrome=False" in source
    assert "render_ai_training_research_command_center(snapshot, ui=ui)" in source

    ui = FakeUi()
    module.render_page(_snapshot(module), ui=ui)
    assert any(name == "title" for name, _value in ui.events)
    assert any(
        name == "subheader" and value == "AI Training Research Command Center"
        for name, value in ui.events
    )
    assert any(name == "dataframe" for name, _value in ui.events)


def test_minimum_valid_snapshot_renders_unknown_without_operational_fabrication() -> None:
    module = _load_page()
    snapshot = valid_snapshot(
        module.EXPECTED_SCHEMA_VERSION,
        module.REQUIRED_SECTIONS,
    )
    ui = FakeUi()
    module.render_page(snapshot, ui=ui)
    rendered = _rendered_html(ui)

    assert "UNKNOWN" in rendered
    assert "RiskManager Authority" in rendered
    assert "Model Promotion" in rendered


def test_page_has_no_direct_runtime_reads_or_operational_imports() -> None:
    source = PAGE.read_text(encoding="utf-8")
    tree = ast.parse(source)

    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )

    forbidden_imports = {
        "ccxt",
        "joblib",
        "pickle",
        "sqlite3",
        "subprocess",
        "requests",
        "httpx",
    }
    assert forbidden_imports.isdisjoint(imports)

    for forbidden in (
        "read_text(",
        "read_bytes(",
        "open(",
        "builder_registry",
        "allow_writes_to_output_dir",
        "send_order",
        "create_order",
    ):
        assert forbidden not in source


def test_visual_branch_does_not_reference_forbidden_builder_or_catalog_paths() -> None:
    source = PAGE.read_text(encoding="utf-8")
    assert "ai_governance_snapshot_builder" not in source
    assert "source_catalog" not in source
    assert "smartcrypto.ops.dashboard_snapshots.ai_training_research_command_center" not in source
