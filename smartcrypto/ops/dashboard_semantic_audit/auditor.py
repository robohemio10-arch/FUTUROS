from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .catalog import (
    HISTORICAL_EXTERNAL_BRAND,
    OFFICIAL_PAGE_CONTRACTS,
    PAGE_UI_TERMS_BY_PATH,
    REQUIRED_ABSENT_TEXT_TERMS,
    SEMANTIC_REQUIREMENTS,
)
from .contracts import (
    DashboardSemanticAuditReport,
    DashboardSemanticFinding,
    DashboardSemanticRequirement,
    SemanticAuditStatus,
    SemanticRequirementStatus,
    normalize_project_path,
)


SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "dashboard_readonly": True,
    "live_locked": True,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "uses_ccxt": False,
    "sends_orders": False,
    "sends_notifications": False,
    "changes_risk": False,
    "changes_model": False,
    "changes_active_signals": False,
    "changes_config": False,
    "runs_ocr": False,
    "rebuilds_dataset": False,
    "changes_readiness": False,
}


def audit_dashboard_semantic_coverage(project_root: str | Path = ".") -> DashboardSemanticAuditReport:
    root = Path(project_root).resolve()
    findings: list[DashboardSemanticFinding] = []
    findings.extend(_audit_page_contracts(root))
    findings.extend(_audit_requirements(root, SEMANTIC_REQUIREMENTS))
    findings.extend(_audit_forbidden_text(root))
    findings.extend(_audit_css_local_only(root))

    failed_required = [
        finding for finding in findings
        if finding.status is SemanticRequirementStatus.FAIL
    ]
    status = SemanticAuditStatus.OK if not failed_required else SemanticAuditStatus.BLOCKED
    summary = {
        "page_count": len(OFFICIAL_PAGE_CONTRACTS),
        "finding_count": len(findings),
        "passed_count": sum(1 for finding in findings if finding.status is SemanticRequirementStatus.PASS),
        "failed_count": len(failed_required),
        "failed_requirement_ids": [finding.requirement_id for finding in failed_required],
    }
    return DashboardSemanticAuditReport(
        status=status,
        summary=summary,
        findings=tuple(findings),
        safety=SAFETY_FLAGS,
    )


def _audit_page_contracts(root: Path) -> list[DashboardSemanticFinding]:
    findings: list[DashboardSemanticFinding] = []
    page_files = sorted((root / "smartcrypto" / "dashboard" / "pages").glob("[0-9][0-9]_*.py"))
    expected_paths = {page.page_path for page in OFFICIAL_PAGE_CONTRACTS}
    actual_paths = {normalize_project_path(path.relative_to(root)) for path in page_files}
    missing_paths = tuple(sorted(expected_paths - actual_paths))
    extra_paths = tuple(sorted(actual_paths - expected_paths))
    findings.append(
        DashboardSemanticFinding(
            requirement_id="page_count_exactly_eight",
            status=SemanticRequirementStatus.PASS if len(actual_paths) == 8 and not missing_paths and not extra_paths else SemanticRequirementStatus.FAIL,
            severity=SEMANTIC_REQUIREMENTS[0].severity,
            description="Exactly the eight canonical Streamlit page files must exist.",
            evidence=tuple(sorted(actual_paths)),
            missing_paths=missing_paths,
            forbidden_terms_found=extra_paths,
        )
    )
    for page in OFFICIAL_PAGE_CONTRACTS:
        findings.append(_check_terms_in_paths(root, f"page_contract:{page.page_id}", page.page_title, (page.page_path,), PAGE_UI_TERMS_BY_PATH[page.page_path]))
    return findings


def _audit_requirements(
    root: Path,
    requirements: Iterable[DashboardSemanticRequirement],
) -> list[DashboardSemanticFinding]:
    return [
        _check_terms_in_paths(
            root,
            requirement.requirement_id,
            requirement.description,
            requirement.paths,
            requirement.required_terms,
            requirement.forbidden_terms,
            severity=requirement.severity,
        )
        for requirement in requirements
    ]


def _check_terms_in_paths(
    root: Path,
    requirement_id: str,
    description: str,
    paths: tuple[str, ...],
    required_terms: tuple[str, ...] = (),
    forbidden_terms: tuple[str, ...] = (),
    *,
    severity: Any | None = None,
) -> DashboardSemanticFinding:
    missing_paths: list[str] = []
    evidence: list[str] = []
    combined_text_parts: list[str] = []
    for relative in paths:
        path = root / relative
        if not path.exists():
            missing_paths.append(relative)
            continue
        evidence.append(relative)
        try:
            combined_text_parts.append(path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            combined_text_parts.append("")
    combined = "\n".join(combined_text_parts)
    missing_terms = tuple(term for term in required_terms if term and term not in combined)
    forbidden_found = tuple(term for term in forbidden_terms if term and term in combined)
    status = SemanticRequirementStatus.PASS if not missing_paths and not missing_terms and not forbidden_found else SemanticRequirementStatus.FAIL
    return DashboardSemanticFinding(
        requirement_id=requirement_id,
        status=status,
        severity=severity or SEMANTIC_REQUIREMENTS[0].severity,
        description=description,
        evidence=tuple(evidence),
        missing_terms=missing_terms,
        forbidden_terms_found=forbidden_found,
        missing_paths=tuple(missing_paths),
    )


def _audit_forbidden_text(root: Path) -> list[DashboardSemanticFinding]:
    text_paths = [
        *sorted((root / "smartcrypto" / "dashboard").rglob("*.py")),
        *sorted((root / "smartcrypto" / "ops" / "dashboard_snapshots").glob("*.py")),
        *sorted((root / "docs").glob("SMART_FUTUROS_COMMAND_CENTER*.md")),
    ]
    forbidden_terms = (*REQUIRED_ABSENT_TEXT_TERMS, HISTORICAL_EXTERNAL_BRAND)
    found: list[str] = []
    evidence: list[str] = []
    for path in text_paths:
        text = path.read_text(encoding="utf-8")
        matches = [term for term in forbidden_terms if term in text]
        if matches:
            evidence.append(normalize_project_path(path.relative_to(root)))
            found.extend(f"{normalize_project_path(path.relative_to(root))}:{term}" for term in matches)
    return [
        DashboardSemanticFinding(
            requirement_id="forbidden_historical_or_deprecated_dashboard_terms_absent",
            status=SemanticRequirementStatus.PASS if not found else SemanticRequirementStatus.FAIL,
            severity=SEMANTIC_REQUIREMENTS[0].severity,
            description="Deprecated external-brand and deprecated alert snapshot terms must be absent from dashboard code/docs.",
            evidence=tuple(evidence),
            forbidden_terms_found=tuple(sorted(found)),
        )
    ]


def _audit_css_local_only(root: Path) -> list[DashboardSemanticFinding]:
    css_path = root / "smartcrypto" / "dashboard" / "assets" / "futuros_command_center.css"
    if not css_path.exists():
        return [
            DashboardSemanticFinding(
                requirement_id="css_local_only",
                status=SemanticRequirementStatus.FAIL,
                severity=SEMANTIC_REQUIREMENTS[0].severity,
                description="Institutional CSS asset must exist and avoid external dependencies.",
                missing_paths=(normalize_project_path(css_path.relative_to(root)),),
            )
        ]
    css = css_path.read_text(encoding="utf-8")
    forbidden = tuple(term for term in ("@import", "http://", "https://", "url(") if term in css)
    return [
        DashboardSemanticFinding(
            requirement_id="css_local_only",
            status=SemanticRequirementStatus.PASS if not forbidden else SemanticRequirementStatus.FAIL,
            severity=SEMANTIC_REQUIREMENTS[0].severity,
            description="Institutional CSS must be local-only: no CDN, external URL or remote import.",
            evidence=("smartcrypto/dashboard/assets/futuros_command_center.css",),
            forbidden_terms_found=forbidden,
        )
    ]
