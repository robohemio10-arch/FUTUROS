"""Research-only Paper Capacity Scaleout V1."""

from .contracts import SAFETY_FLAGS, SCHEMA_VERSION, CapacityScaleoutConfig
from .engine import (
    PaperCapacityScaleoutError,
    build_paper_capacity_scaleout_v1,
    evaluate_capacity_scenarios,
)
from .persistence import (
    resolve_report_markdown_path,
    resolve_report_path,
    resolve_research_path,
    write_research_rows,
    write_report,
)

__all__ = [
    "SAFETY_FLAGS",
    "SCHEMA_VERSION",
    "CapacityScaleoutConfig",
    "PaperCapacityScaleoutError",
    "build_paper_capacity_scaleout_v1",
    "evaluate_capacity_scenarios",
    "resolve_report_markdown_path",
    "resolve_report_path",
    "resolve_research_path",
    "write_research_rows",
    "write_report",
]
