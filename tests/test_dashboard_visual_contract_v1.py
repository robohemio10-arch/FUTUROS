from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGES = sorted((ROOT / "smartcrypto" / "dashboard" / "pages").glob("[0-9][0-9]_*.py"))
EXPECTED = {
    "01_infrastructure.py": "dashboard_infrastructure_snapshot.json",
    "02_portfolio_risk.py": "dashboard_portfolio_risk_snapshot.json",
    "03_grid_monitor.py": "dashboard_grid_monitor_snapshot.json",
    "04_opportunity_scanner.py": "dashboard_opportunity_scanner_snapshot.json",
    "05_ai_governance.py": "dashboard_ai_governance_snapshot.json",
    "06_active_controls.py": "dashboard_active_controls_snapshot.json",
    "07_quantitative_reports.py": "dashboard_quantitative_reports_snapshot.json",
    "08_alerts_messaging.py": "dashboard_alerts_messaging_snapshot.json",
}
THEME_CALLS = (
    "inject_smart_futuros_command_center_css",
    "render_global_topbar",
    "render_page_title",
    "render_sidebar",
    "render_footer_audit_bar",
)


def test_eight_pages_use_shared_theme_and_canonical_snapshots() -> None:
    assert len(PAGES) == 8
    for path in PAGES:
        source = path.read_text(encoding="utf-8")
        assert EXPECTED[path.name] in source
        assert all(name in source for name in THEME_CALLS)
        assert "SMART FUTUROS" in source or "smartcrypto.dashboard.ui" in source
        assert "UNKNOWN" in source


def test_visual_contract_has_no_deprecated_snapshot_or_historical_brand() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in PAGES)
    assert "dashboard_alerts_queue_snapshot.json" not in source
    assert "BlackRock" not in source
