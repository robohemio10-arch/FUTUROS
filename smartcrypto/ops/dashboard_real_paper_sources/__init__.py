"""SMART FUTUROS dashboard real paper data sources package."""

from __future__ import annotations

from smartcrypto.ops.dashboard_real_paper_sources.builder import (
    SCHEMA_VERSION,
    BuildResult,
    build_real_paper_sources_snapshot,
)

__all__ = [
    "SCHEMA_VERSION",
    "BuildResult",
    "build_real_paper_sources_snapshot",
]
