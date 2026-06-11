from __future__ import annotations

from smartcrypto.dashboard.services.page_snapshot_loader import PAGE_SNAPSHOT_SPECS, load_page_snapshot
from smartcrypto.ops.dashboard_snapshots.contracts import DashboardPageId
from tests.dashboard_page_test_support import FakeUi, load_page_module, page_paths, valid_snapshot


def test_page_schema_contract_matches_loader_registry() -> None:
    for path in page_paths():
        module = load_page_module(path)
        page_id = DashboardPageId(path.stem[3:])
        spec = PAGE_SNAPSHOT_SPECS[page_id]
        assert module.EXPECTED_SCHEMA_VERSION == spec.schema_version
        assert module.SNAPSHOT_PATH.endswith(spec.filename)


def test_missing_snapshot_returns_unknown_without_writing(tmp_path) -> None:
    for page_id, spec in PAGE_SNAPSHOT_SPECS.items():
        snapshot = load_page_snapshot(page_id, project_root=tmp_path)
        assert snapshot["status"] == "UNKNOWN"
        assert snapshot["schema_version"] == spec.schema_version
        assert snapshot["dashboard_readonly"] is True
        assert snapshot["order_submission_enabled"] is False
    assert not (tmp_path / "data").exists()


def test_pages_accept_minimal_valid_snapshot_and_optional_fields_absent() -> None:
    for path in page_paths():
        module = load_page_module(path)
        snapshot = valid_snapshot(module.EXPECTED_SCHEMA_VERSION, module.REQUIRED_SECTIONS)
        snapshot["sections"] = {"audit": {"status": "OK", "reason": "minimal"}}
        module.render_page(snapshot, ui=FakeUi())


def test_alerts_page_uses_only_messaging_snapshot_name() -> None:
    module = load_page_module(next(path for path in page_paths() if path.name.startswith("08_")))
    assert module.SNAPSHOT_PATH.endswith("dashboard_alerts_messaging_snapshot.json")
    dashboard_text = "\n".join(path.read_text(encoding="utf-8") for path in page_paths())
    assert "dashboard_alerts_queue_snapshot.json" not in dashboard_text
