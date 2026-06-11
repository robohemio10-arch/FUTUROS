"""Read-only services for SMART FUTUROS dashboard views."""

from .page_snapshot_loader import PAGE_SNAPSHOT_SPECS, PageSnapshotSpec, load_page_snapshot

__all__ = ["PAGE_SNAPSHOT_SPECS", "PageSnapshotSpec", "load_page_snapshot"]
