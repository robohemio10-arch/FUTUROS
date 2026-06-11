"""Semantic coverage audit for the SMART FUTUROS Command Center."""

from .auditor import audit_dashboard_semantic_coverage
from .contracts import (
    DASHBOARD_SEMANTIC_AUDIT_SCHEMA_VERSION,
    DashboardSemanticAuditReport,
    DashboardSemanticFinding,
    DashboardSemanticPageContract,
    DashboardSemanticRequirement,
    SemanticAuditStatus,
    SemanticRequirementSeverity,
    SemanticRequirementStatus,
)

__all__ = [
    "DASHBOARD_SEMANTIC_AUDIT_SCHEMA_VERSION",
    "DashboardSemanticAuditReport",
    "DashboardSemanticFinding",
    "DashboardSemanticPageContract",
    "DashboardSemanticRequirement",
    "SemanticAuditStatus",
    "SemanticRequirementSeverity",
    "SemanticRequirementStatus",
    "audit_dashboard_semantic_coverage",
]
