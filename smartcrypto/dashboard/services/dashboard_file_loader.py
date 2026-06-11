from __future__ import annotations

from pathlib import Path

from smartcrypto.ops.dashboard_snapshots.contracts import DashboardLoadResult, SourceKind
from smartcrypto.ops.dashboard_snapshots.file_loader import load_dashboard_file


class DashboardFileLoader:
    """Small UI-facing wrapper around the read-only snapshot file loader."""

    def __init__(self, project_root: str | Path = ".") -> None:
        self.project_root = Path(project_root).resolve()

    def load(
        self,
        path: str | Path,
        source_kind: SourceKind = SourceKind.OPTIONAL_EXISTING_SOURCE,
    ) -> DashboardLoadResult:
        target = Path(path)
        if not target.is_absolute():
            target = self.project_root / target
        return load_dashboard_file(target, source_kind)


def load_dashboard_source(
    path: str | Path,
    *,
    project_root: str | Path = ".",
    source_kind: SourceKind = SourceKind.OPTIONAL_EXISTING_SOURCE,
) -> DashboardLoadResult:
    return DashboardFileLoader(project_root).load(path, source_kind)
