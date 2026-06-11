from __future__ import annotations

import importlib
from pathlib import Path

from smartcrypto.dashboard.services.dashboard_file_loader import DashboardFileLoader
from smartcrypto.dashboard.services.dashboard_snapshot_service import load_dashboard_snapshot
from smartcrypto.ops.dashboard_snapshots.contracts import DashboardSectionStatus, SourceKind
from smartcrypto.ops.dashboard_snapshots.file_loader import load_dashboard_file


FIXTURES = Path(__file__).parent / "fixtures"


def test_missing_sources_return_status_by_source_kind_without_creating_paths(tmp_path: Path) -> None:
    target = tmp_path / "runtime" / "missing.json"

    required = load_dashboard_file(target, SourceKind.REQUIRED_EXISTING_SOURCE)
    optional = load_dashboard_file(target, SourceKind.OPTIONAL_EXISTING_SOURCE)
    future = load_dashboard_file(target, SourceKind.FUTURE_SOURCE)

    assert required.status is DashboardSectionStatus.MISSING_REQUIRED
    assert optional.status is DashboardSectionStatus.MISSING_OPTIONAL
    assert future.status is DashboardSectionStatus.UNKNOWN
    assert not target.parent.exists()


def test_valid_json_and_jsonl_load_readonly() -> None:
    json_result = load_dashboard_file(FIXTURES / "dashboard_sources" / "sample.json")
    jsonl_result = load_dashboard_file(FIXTURES / "dashboard_sources" / "sample.jsonl")

    assert json_result.status is DashboardSectionStatus.OK
    assert json_result.data["source"] == "synthetic_fixture"
    assert jsonl_result.status is DashboardSectionStatus.OK
    assert [row["sequence"] for row in jsonl_result.data] == [1, 2]


def test_invalid_json_returns_controlled_error() -> None:
    result = load_dashboard_file(FIXTURES / "dashboard_sources" / "invalid.json")

    assert result.status is DashboardSectionStatus.ERROR
    assert result.exists is True
    assert "JSONDecodeError" in (result.error or "")


def test_missing_parquet_does_not_import_optional_dependency(tmp_path: Path) -> None:
    result = load_dashboard_file(tmp_path / "missing.parquet")
    assert result.status is DashboardSectionStatus.MISSING_OPTIONAL


def test_parquet_without_pyarrow_returns_controlled_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "fixture.parquet"
    target.write_bytes(b"synthetic-not-a-real-parquet")
    real_import = importlib.import_module

    def blocked_import(name: str, package: str | None = None):
        if name == "pyarrow":
            raise ImportError("pyarrow unavailable")
        return real_import(name, package)

    monkeypatch.setattr(importlib, "import_module", blocked_import)
    result = load_dashboard_file(target)

    assert result.status is DashboardSectionStatus.ERROR
    assert "ImportError" in (result.error or "")


def test_ui_wrapper_and_snapshot_service_only_read_existing_files(tmp_path: Path) -> None:
    snapshot_path = FIXTURES / "dashboard_snapshots" / "valid_snapshot.json"
    loader = DashboardFileLoader(FIXTURES.parent)

    result = loader.load(snapshot_path)
    snapshot = load_dashboard_snapshot(snapshot_path, "dashboard_infrastructure_snapshot_v1")

    assert result.status is DashboardSectionStatus.OK
    assert snapshot["runtime_mode"] == "paper"
    assert list(tmp_path.iterdir()) == []


def test_missing_snapshot_is_unknown_and_fail_closed(tmp_path: Path) -> None:
    snapshot = load_dashboard_snapshot(tmp_path / "missing.json", "expected_v1")

    assert snapshot["sections"]["snapshot"]["status"] == "UNKNOWN"
    assert snapshot["live_locked"] is True
    assert snapshot["order_submission_enabled"] is False
    assert snapshot["real_order_submission_enabled"] is False
