from __future__ import annotations

from smartcrypto.ops.dashboard_snapshots.contracts import DashboardPageId, SourceKind
from smartcrypto.ops.dashboard_snapshots.source_catalog import (
    DASHBOARD_SNAPSHOT_FILENAMES,
    SOURCE_CATALOG,
    iter_sources,
)


def test_catalog_contains_all_eight_pages_and_valid_source_kinds() -> None:
    assert set(SOURCE_CATALOG) == set(DashboardPageId)
    assert all(source.source_kind in SourceKind for source in iter_sources())
    assert all(SOURCE_CATALOG[page] for page in DashboardPageId)


def test_final_snapshot_names_are_canonical() -> None:
    assert len(DASHBOARD_SNAPSHOT_FILENAMES) == 8
    assert (
        DASHBOARD_SNAPSHOT_FILENAMES[DashboardPageId.alerts_messaging]
        == "dashboard_alerts_messaging_snapshot.json"
    )
    assert "dashboard_alerts_queue_snapshot.json" not in DASHBOARD_SNAPSHOT_FILENAMES.values()


def test_generated_snapshots_are_runtime_sources_not_versioned_inputs() -> None:
    for page, filename in DASHBOARD_SNAPSHOT_FILENAMES.items():
        generated = [
            source
            for source in SOURCE_CATALOG[page]
            if source.source_kind is SourceKind.GENERATED_BY_THIS_BRANCH
        ]
        assert [source.path for source in generated] == [f"data/reports/{filename}"]


def test_future_sources_are_explicitly_non_required() -> None:
    future = [source for source in iter_sources() if source.source_kind is SourceKind.FUTURE_SOURCE]
    assert future
    assert all(source.source_kind is not SourceKind.REQUIRED_EXISTING_SOURCE for source in future)
