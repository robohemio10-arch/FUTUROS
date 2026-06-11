from __future__ import annotations

from collections.abc import Iterable

from .contracts import SoakEvidenceSource

EVIDENCE_SOURCES: tuple[SoakEvidenceSource, ...] = (
    SoakEvidenceSource(
        name="paper_shadow_soak_report",
        path="data/reports/paper_shadow_soak_report.json",
        required_for_accounting=True,
        required_for_readiness=True,
        description="Canonical paper/shadow soak report.",
    ),
    SoakEvidenceSource(
        name="paper_shadow_soak_continuity_audit",
        path="data/reports/paper_shadow_soak_continuity_audit.json",
        required_for_accounting=True,
        required_for_readiness=True,
        description="Existing continuity audit report.",
    ),
    SoakEvidenceSource(
        name="paper_shadow_soak_anchor_continuity_pack",
        path="data/reports/paper_shadow_soak_anchor_continuity_pack.json",
        required_for_accounting=True,
        required_for_readiness=True,
        description="Read-only continuity anchor pack.",
    ),
    SoakEvidenceSource(
        name="runtime_evidence_pack_v2",
        path="data/reports/runtime_evidence_pack_v2.json",
        required_for_accounting=True,
        required_for_readiness=True,
        description="Runtime evidence pack v2.",
    ),
    SoakEvidenceSource(
        name="readiness_snapshot_v2",
        path="data/reports/readiness_snapshot_v2.json",
        required_for_accounting=True,
        required_for_readiness=True,
        description="Readiness snapshot v2.",
    ),
    SoakEvidenceSource(
        name="paper_soak_report",
        path="data/reports/paper_soak_report.json",
        required_for_accounting=False,
        required_for_readiness=False,
        description="Legacy paper-only soak report.",
    ),
    SoakEvidenceSource(
        name="freqtrade_paper_db_authority_report",
        path="data/reports/freqtrade_paper_db_authority_report.json",
        required_for_accounting=False,
        required_for_readiness=True,
        description="Authorized Freqtrade paper DB resolver output.",
    ),
    SoakEvidenceSource(
        name="monte_carlo_risk_simulation_report",
        path="data/reports/monte_carlo_risk_simulation_report.json",
        required_for_accounting=False,
        required_for_readiness=True,
        description="Monte Carlo readiness evidence.",
    ),
    SoakEvidenceSource(
        name="dashboard_snapshot_build_summary",
        path="data/reports/dashboard_snapshot_build_summary.json",
        required_for_accounting=False,
        required_for_readiness=False,
        description="Dashboard snapshot build summary.",
    ),
    SoakEvidenceSource(
        name="dashboard_semantic_coverage_audit",
        path="docs/SMART_FUTUROS_DASHBOARD_SEMANTIC_COVERAGE_AUDIT_V2.md",
        required_for_accounting=True,
        required_for_readiness=False,
        description="Versioned dashboard semantic closeout evidence.",
    ),
)

ANCHOR_FAMILY_NAMES = frozenset(
    {
        "paper_shadow_soak_report",
        "paper_shadow_soak_continuity_audit",
        "paper_shadow_soak_anchor_continuity_pack",
        "runtime_evidence_pack_v2",
        "readiness_snapshot_v2",
    }
)


def iter_soak_gap_accounting_sources() -> Iterable[SoakEvidenceSource]:
    return iter(EVIDENCE_SOURCES)
