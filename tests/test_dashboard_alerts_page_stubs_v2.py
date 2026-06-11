from __future__ import annotations

from tests.dashboard_page_test_support import FakeUi, load_page_module, page_paths, valid_snapshot


def test_alerts_page_renders_routing_and_unsent_stub() -> None:
    path = next(path for path in page_paths() if path.name.startswith("08_"))
    module = load_page_module(path)
    ui = FakeUi()
    module.render_page(valid_snapshot(module.EXPECTED_SCHEMA_VERSION, module.REQUIRED_SECTIONS), ui=ui)
    rendered = "\n".join(str(value) for _name, value in ui.events)
    assert "NO TELEGRAM/NTFY SEND" in rendered
    assert "delivery_attempted" in rendered
    assert module.SNAPSHOT_PATH.endswith("dashboard_alerts_messaging_snapshot.json")


def test_alerts_page_has_no_network_or_token_access() -> None:
    path = next(path for path in page_paths() if path.name.startswith("08_"))
    text = path.read_text(encoding="utf-8")
    for token in ("requests", "httpx", "aiohttp", "TOKEN", "dashboard_alerts_queue_snapshot"):
        assert token not in text
