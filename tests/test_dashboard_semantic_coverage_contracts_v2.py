from __future__ import annotations

from smartcrypto.ops.dashboard_semantic_audit.catalog import (
    OFFICIAL_PAGE_CONTRACTS,
    REQUIRED_STUB_FILES,
    REQUIRED_UI_FILES,
    SEMANTIC_REQUIREMENTS,
)
from smartcrypto.ops.dashboard_semantic_audit.contracts import (
    DASHBOARD_SEMANTIC_AUDIT_SCHEMA_VERSION,
    DashboardSemanticFinding,
    SemanticRequirementSeverity,
    SemanticRequirementStatus,
)


def test_semantic_audit_contracts_are_canonical() -> None:
    assert DASHBOARD_SEMANTIC_AUDIT_SCHEMA_VERSION == "dashboard_semantic_coverage_audit_v2"
    assert len(OFFICIAL_PAGE_CONTRACTS) == 8
    assert {page.page_number for page in OFFICIAL_PAGE_CONTRACTS} == {
        "01", "02", "03", "04", "05", "06", "07", "08",
    }
    assert all(page.snapshot_filename.startswith("dashboard_") for page in OFFICIAL_PAGE_CONTRACTS)
    assert all(page.snapshot_filename.endswith("_snapshot.json") for page in OFFICIAL_PAGE_CONTRACTS)
    assert REQUIRED_UI_FILES
    assert REQUIRED_STUB_FILES
    assert SEMANTIC_REQUIREMENTS


def test_semantic_finding_serializes_without_runtime_payloads() -> None:
    finding = DashboardSemanticFinding(
        requirement_id="sample",
        status=SemanticRequirementStatus.PASS,
        severity=SemanticRequirementSeverity.REQUIRED,
        description="sample requirement",
        evidence=("smartcrypto/dashboard/app.py",),
    )
    payload = finding.to_dict()
    assert payload["status"] == "PASS"
    assert payload["severity"] == "REQUIRED"
    assert payload["evidence"] == ["smartcrypto/dashboard/app.py"]
