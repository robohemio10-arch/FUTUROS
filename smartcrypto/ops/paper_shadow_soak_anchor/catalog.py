from __future__ import annotations

from smartcrypto.ops.paper_shadow_soak_anchor.contracts import SoakEvidenceSource


SOAK_EVIDENCE_SOURCES: tuple[SoakEvidenceSource, ...] = (
    SoakEvidenceSource(
        name="paper_shadow_soak_report",
        path="data/reports/paper_shadow_soak_report.json",
        required_for_anchor=True,
        required_for_readiness=True,
        description="Canonical paper/shadow soak report with observed days and readiness blockers.",
    ),
    SoakEvidenceSource(
        name="paper_shadow_soak_continuity_audit",
        path="data/reports/paper_shadow_soak_continuity_audit.json",
        required_for_anchor=True,
        required_for_readiness=True,
        description="Continuity and gap accounting report for paper/shadow operation.",
    ),
    SoakEvidenceSource(
        name="runtime_evidence_pack_v2",
        path="data/reports/runtime_evidence_pack_v2.json",
        required_for_anchor=True,
        required_for_readiness=True,
        description="Runtime evidence pack v2, read-only operational evidence source.",
    ),
    SoakEvidenceSource(
        name="readiness_snapshot_v2",
        path="data/reports/readiness_snapshot_v2.json",
        required_for_anchor=True,
        required_for_readiness=True,
        description="Readiness snapshot v2 with explicit live/canary hard blocks.",
    ),
    SoakEvidenceSource(
        name="paper_soak_report",
        path="data/reports/paper_soak_report.json",
        required_for_anchor=False,
        required_for_readiness=False,
        description="Legacy/canonical paper soak report used as compatibility evidence.",
    ),
    SoakEvidenceSource(
        name="freqtrade_paper_db_authority_report",
        path="data/reports/freqtrade_paper_db_authority_report.json",
        required_for_anchor=False,
        required_for_readiness=True,
        description="Evidence that the paper DB source of truth is resolved without live exchange access.",
    ),
    SoakEvidenceSource(
        name="monte_carlo_risk_simulation_report",
        path="data/reports/monte_carlo_risk_simulation_report.json",
        required_for_anchor=False,
        required_for_readiness=True,
        description="Monte Carlo/no-trade policy evidence for readiness gating.",
    ),
    SoakEvidenceSource(
        name="dashboard_semantic_coverage_audit_cli",
        path="scripts/audit_dashboard_semantic_coverage_v2.py",
        required_for_anchor=True,
        required_for_readiness=False,
        description="Versioned semantic coverage audit CLI proving Command Center closeout.",
    ),
    SoakEvidenceSource(
        name="dashboard_semantic_coverage_audit_doc",
        path="docs/SMART_FUTUROS_DASHBOARD_SEMANTIC_COVERAGE_AUDIT_V2.md",
        required_for_anchor=True,
        required_for_readiness=False,
        description="Versioned semantic closeout documentation for the dashboard phase.",
    ),
    SoakEvidenceSource(
        name="dashboard_snapshot_build_summary",
        path="data/reports/dashboard_snapshot_build_summary.json",
        required_for_anchor=False,
        required_for_readiness=False,
        description="Optional snapshot build summary consumed by the institutional dashboard.",
    ),
)


ANCHOR_FAMILY_NAMES = frozenset(
    {
        "paper_shadow_soak_report",
        "paper_shadow_soak_continuity_audit",
        "runtime_evidence_pack_v2",
        "readiness_snapshot_v2",
        "paper_soak_report",
    }
)


def iter_soak_evidence_sources() -> tuple[SoakEvidenceSource, ...]:
    return SOAK_EVIDENCE_SOURCES
