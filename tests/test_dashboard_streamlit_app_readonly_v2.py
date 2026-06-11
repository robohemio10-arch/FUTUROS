from __future__ import annotations

import importlib

from smartcrypto.dashboard.app import DASHBOARD_TITLE, PAGE_LINKS, load_shell_snapshots, render_app
from tests.dashboard_page_test_support import FakeUi, valid_snapshot


def test_app_is_import_safe_and_declares_command_center() -> None:
    module = importlib.import_module("smartcrypto.dashboard.app")
    assert callable(module.main)
    assert DASHBOARD_TITLE == "SMART FUTUROS Command Center"
    assert len(PAGE_LINKS) == 8


def test_app_missing_snapshots_are_controlled_unknown(tmp_path) -> None:
    global_snapshot, build_summary = load_shell_snapshots(tmp_path)
    assert global_snapshot["status"] == "UNKNOWN"
    assert build_summary["status"] == "UNKNOWN"
    render_app(global_snapshot, build_summary, ui=FakeUi())
    assert not (tmp_path / "data").exists()


def test_app_renders_eight_readonly_page_links() -> None:
    global_snapshot = valid_snapshot("dashboard_global_status_snapshot_v1", ("pages",))
    build_summary = valid_snapshot("dashboard_snapshot_build_summary_v1", ())
    build_summary["generated_files"] = []
    ui = FakeUi()
    render_app(global_snapshot, build_summary, ui=ui)
    links = [event for event in ui.events if event[0] == "page_link"]
    assert len(links) == 8


def test_app_source_is_snapshot_only() -> None:
    source = importlib.import_module("smartcrypto.dashboard.app").__file__
    assert source is not None
    text = open(source, encoding="utf-8").read()
    forbidden = ("read_parquet", "sqlite3", "load_yaml", "read_jsonl", "requests.post", "httpx.post")
    assert all(token not in text for token in forbidden)
