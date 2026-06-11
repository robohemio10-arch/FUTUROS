from __future__ import annotations

from tests.dashboard_page_test_support import FakeUi, load_page_module, page_paths, valid_snapshot


def test_active_controls_page_renders_stub_governance_and_readiness() -> None:
    path = next(path for path in page_paths() if path.name.startswith("06_"))
    module = load_page_module(path)
    ui = FakeUi()
    module.render_page(valid_snapshot(module.EXPECTED_SCHEMA_VERSION, module.REQUIRED_SECTIONS), ui=ui)
    rendered = "\n".join(str(value) for _name, value in ui.events)
    assert "STUB ONLY" in rendered
    assert "N4 HARD_BLOCKED" in rendered
    assert "Readiness & Gates" in rendered
    assert "LIVE_ORDER: disabled (HARD_BLOCKED)" in rendered


def test_active_controls_page_has_no_operational_adapter() -> None:
    path = next(path for path in page_paths() if path.name.startswith("06_"))
    text = path.read_text(encoding="utf-8")
    for token in ("command_bus", "yaml", "create_order", "set_kill_switch", "ENABLE_CANARY_RELEASE"):
        assert token not in text
