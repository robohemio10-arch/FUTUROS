"""Research-only remediation analysis for paper shadow survivor rules."""

from .remediation import (
    DEFAULT_IMPACT_REPORT,
    DEFAULT_MARKDOWN_REPORT,
    DEFAULT_OUTPUT_REPORT,
    SCHEMA_VERSION,
    build_paper_shadow_survivor_remediation_research_report,
    compute_survivor_remediation,
    load_remediation_inputs,
)

__all__ = [
    "DEFAULT_IMPACT_REPORT",
    "DEFAULT_MARKDOWN_REPORT",
    "DEFAULT_OUTPUT_REPORT",
    "SCHEMA_VERSION",
    "build_paper_shadow_survivor_remediation_research_report",
    "compute_survivor_remediation",
    "load_remediation_inputs",
]
