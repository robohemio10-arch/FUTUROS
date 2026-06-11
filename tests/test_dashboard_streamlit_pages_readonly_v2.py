from __future__ import annotations

from tests.dashboard_page_test_support import FakeUi, load_page_module, page_paths, valid_snapshot


EXPECTED_SNAPSHOTS = {
    "01_infrastructure.py": "dashboard_infrastructure_snapshot.json",
    "02_portfolio_risk.py": "dashboard_portfolio_risk_snapshot.json",
    "03_grid_monitor.py": "dashboard_grid_monitor_snapshot.json",
    "04_opportunity_scanner.py": "dashboard_opportunity_scanner_snapshot.json",
    "05_ai_governance.py": "dashboard_ai_governance_snapshot.json",
    "06_active_controls.py": "dashboard_active_controls_snapshot.json",
    "07_quantitative_reports.py": "dashboard_quantitative_reports_snapshot.json",
    "08_alerts_messaging.py": "dashboard_alerts_messaging_snapshot.json",
}


def test_eight_pages_exist_and_are_import_safe() -> None:
    paths = page_paths()
    assert len(paths) == 8
    assert {path.name for path in paths} == set(EXPECTED_SNAPSHOTS)
    for path in paths:
        module = load_page_module(path)
        assert callable(module.main)
        assert callable(module.render_page)
        assert callable(module.render_missing_snapshot)


def test_each_page_references_exact_snapshot_and_readonly_loader() -> None:
    for path in page_paths():
        text = path.read_text(encoding="utf-8")
        assert EXPECTED_SNAPSHOTS[path.name] in text
        assert "load_page_snapshot" in text
        assert "render_snapshot_page" in text
        assert "if __name__ == \"__main__\":" in text


def test_each_page_renders_minimum_snapshot_without_external_runtime() -> None:
    for path in page_paths():
        module = load_page_module(path)
        ui = FakeUi()
        module.render_page(valid_snapshot(module.EXPECTED_SCHEMA_VERSION, module.REQUIRED_SECTIONS), ui=ui)
        assert any(name == "title" for name, _value in ui.events)
        assert any(name == "dataframe" for name, _value in ui.events)


def test_pages_do_not_reference_backend_generation() -> None:
    forbidden = ("builder_registry", "scripts/build_dashboard", "allow_writes_to_output_dir")
    for path in page_paths():
        text = path.read_text(encoding="utf-8")
        assert all(token not in text for token in forbidden)
