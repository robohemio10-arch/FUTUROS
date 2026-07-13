"""Deterministic, read-only remediation planning for the legacy Master boundary."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess  # nosec B404 - fixed local Git metadata queries only
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from .legacy_master_governance import (
    DEFAULT_MASTER,
    DEFAULT_POLICY,
    POLICY_SCHEMA_VERSION,
    REPORT_SCHEMA_VERSION as SOURCE_REPORT_SCHEMA_VERSION,
    FindingClassification,
    PolicyError,
    Severity,
    build_legacy_master_boundary_report,
    load_legacy_master_policy,
    verify_legacy_master_policy,
)


TAXONOMY_SCHEMA_VERSION = "trader_master_legacy_boundary_remediation_taxonomy_v1"
PLAN_SCHEMA_VERSION = "trader_master_legacy_boundary_remediation_plan_report_v1"
DEFAULT_TAXONOMY = Path(
    "config/trader_master_legacy_boundary_remediation_taxonomy_v1.json"
)
DEFAULT_JSON_REPORT = Path(
    "data/reports/trader_master_legacy_boundary_remediation_plan_v1.json"
)
DEFAULT_MARKDOWN_REPORT = Path(
    "data/reports/trader_master_legacy_boundary_remediation_plan_v1.md"
)
POLICY_RELATIVE_PATH = "config/trader_master_legacy_research_only_policy_v1.json"
FINGERPRINT_SPEC_PATH = (
    "smartcrypto/data/trader_master_fingerprint_v2/fingerprint_spec.py"
)
LEGACY_WRITER_PATH = "smartcrypto/data/trades_importer.py"

RELEVANT_CLASSIFICATIONS = frozenset(
    {
        FindingClassification.DYNAMIC_REFERENCE_UNRESOLVED.value,
        FindingClassification.LEGACY_WRITER_IMPLEMENTATION.value,
        FindingClassification.LEGACY_WRITER_CALLSITE.value,
        FindingClassification.DIRECT_MASTER_WRITE.value,
        FindingClassification.DIRECT_MASTER_IMPORT.value,
        FindingClassification.UNREGISTERED_MASTER_CONSUMER.value,
        FindingClassification.PROHIBITED_OPERATIONAL_CONSUMER.value,
        FindingClassification.FINGERPRINT_V2_MISCLASSIFICATION.value,
        FindingClassification.CONFIGURATION_REFERENCE.value,
    }
)
WRITER_CLASSIFICATIONS = frozenset(
    {
        FindingClassification.DIRECT_MASTER_WRITE.value,
        FindingClassification.DIRECT_MASTER_IMPORT.value,
        FindingClassification.LEGACY_WRITER_CALLSITE.value,
    }
)
READ_EVIDENCE_TOKENS = frozenset(
    {
        "literal_master_reference",
        "path_targets_legacy_master",
        "reader_targets_legacy_master",
        "configuration_points_to_legacy_master",
    }
)
INSTITUTIONAL_READER_TOKENS = frozenset(
    {
        "read_trader_master_readonly",
        "institutional_readonly_adapter",
        "institutional_readonly_master_adapter",
    }
)
REQUIRED_BRANCH_NAMES = (
    "trader-master-boundary-writer-callsite-removal-v1",
    "trader-master-boundary-operational-reference-isolation-v1",
    "trader-master-boundary-dynamic-reference-resolution-v1",
    "trader-master-boundary-readonly-adapter-migration-v1",
    "trader-master-boundary-readonly-consumer-registration-v1",
    "trader-master-boundary-reverification-closeout-v1",
)
REQUIRED_GLOBAL_FORBIDDEN_CAPABILITIES = frozenset(
    {
        "bridge",
        "import",
        "write_trader_master",
        "fingerprint_generation",
        "operational_training",
        "paper_signal_selection",
        "live_signal_selection",
        "risk_decision",
        "order_execution",
        "model_promotion",
    }
)
PLAN_SAFETY_FLAGS: dict[str, bool] = {
    "remediation_applied": False,
    "branches_created": False,
    "policy_updated": False,
    "consumers_registered": False,
    "writer_calls_removed": False,
    "operational_references_changed": False,
    "dynamic_references_resolved": False,
    "bridge_authorized": False,
    "import_authorized": False,
    "write_authorized": False,
    "fingerprint_generation_allowed": False,
    "operational_training_authorized": False,
    "paper_signal_selection_authorized": False,
    "live_signal_selection_authorized": False,
    "risk_decision_authorized": False,
    "order_execution_authorized": False,
    "operational_authority": False,
    "writes_trader_master": False,
    "writes_parquet": False,
    "writes_xlsx": False,
    "writes_csv": False,
    "writes_sqlite": False,
    "writes_runtime": False,
    "changes_fingerprint_spec": False,
    "sends_orders": False,
    "exchange_private_access": False,
}


class RemediationAction(StrEnum):
    REGISTER_AS_READONLY_RESEARCH_CONSUMER = "REGISTER_AS_READONLY_RESEARCH_CONSUMER"
    REFACTOR_TO_INSTITUTIONAL_READONLY_ADAPTER = (
        "REFACTOR_TO_INSTITUTIONAL_READONLY_ADAPTER"
    )
    REMOVE_LEGACY_WRITER_CALLSITE = "REMOVE_LEGACY_WRITER_CALLSITE"
    ISOLATE_OPERATIONAL_REFERENCE = "ISOLATE_OPERATIONAL_REFERENCE"
    RESOLVE_DYNAMIC_REFERENCE = "RESOLVE_DYNAMIC_REFERENCE"
    KEEP_QUARANTINED_LEGACY_IMPLEMENTATION = "KEEP_QUARANTINED_LEGACY_IMPLEMENTATION"
    FALSE_POSITIVE_WITH_STRUCTURAL_PROOF = "FALSE_POSITIVE_WITH_STRUCTURAL_PROOF"
    BLOCKED_REQUIRES_MANUAL_REVIEW = "BLOCKED_REQUIRES_MANUAL_REVIEW"


class RemediationDecision(StrEnum):
    PLAN_READY = "LEGACY_BOUNDARY_REMEDIATION_PLAN_READY"
    REQUIRES_MANUAL_REVIEW = "LEGACY_BOUNDARY_REMEDIATION_PLAN_REQUIRES_MANUAL_REVIEW"
    PLAN_INCOMPLETE = "LEGACY_BOUNDARY_REMEDIATION_PLAN_INCOMPLETE"
    NOT_REQUIRED = "LEGACY_BOUNDARY_REMEDIATION_NOT_REQUIRED"
    SOURCE_AUDIT_BLOCKED = "LEGACY_BOUNDARY_SOURCE_AUDIT_BLOCKED"


class TaxonomyError(ValueError):
    """Raised when the remediation taxonomy cannot be safely loaded."""


@dataclass(frozen=True)
class StructuralProof:
    structural_proof: str
    proof_method: str
    proof_reproducible: bool


@dataclass(frozen=True)
class FindingReference:
    source_finding_id: str
    finding_id: str
    classification: str
    severity: str
    relative_path: str
    line_number: int
    symbol: str
    evidence_token: str

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "classification": self.classification,
            "relative_path": self.relative_path,
            "line_number": self.line_number,
            "symbol": self.symbol,
            "evidence_token": self.evidence_token,
        }


@dataclass(frozen=True)
class RemediationItem:
    finding_id: str
    finding_classification: str
    source_severity: str
    relative_path: str
    line_number: int
    symbol: str
    namespace: str
    source_evidence: str
    recommended_action: RemediationAction
    action_precedence: int
    rationale_code: str
    rationale: str
    structural_proof: StructuralProof | None
    requires_code_change: bool
    future_policy_change_required: bool
    requires_manual_review: bool
    proposed_branch_id: str
    allowed_file_scope: tuple[str, ...]
    forbidden_file_scope: tuple[str, ...]
    required_tests: tuple[str, ...]
    expected_boundary_delta: str
    safety_flags: tuple[tuple[str, bool], ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["recommended_action"] = self.recommended_action.value
        payload["structural_proof"] = (
            asdict(self.structural_proof) if self.structural_proof else None
        )
        payload["allowed_file_scope"] = list(self.allowed_file_scope)
        payload["forbidden_file_scope"] = list(self.forbidden_file_scope)
        payload["required_tests"] = list(self.required_tests)
        payload["safety_flags"] = dict(self.safety_flags)
        return payload


@dataclass(frozen=True)
class RemediationDependency:
    branch_id: str
    depends_on_branch_id: str
    rationale: str


@dataclass(frozen=True)
class BranchRemediationPackage:
    branch_id: str
    proposed_branch_name: str
    sequence: int
    actions: tuple[str, ...]
    finding_ids: tuple[str, ...]
    target_files: tuple[str, ...]
    allowed_file_scope: tuple[str, ...]
    forbidden_file_scope: tuple[str, ...]
    dependencies: tuple[str, ...]
    entry_gate: tuple[str, ...]
    exit_gate: tuple[str, ...]
    required_tests: tuple[str, ...]
    expected_boundary_delta: str
    policy_update_allowed: bool
    implementation_authorized: bool = False
    branch_created: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for field in (
            "actions",
            "finding_ids",
            "target_files",
            "allowed_file_scope",
            "forbidden_file_scope",
            "dependencies",
            "entry_gate",
            "exit_gate",
            "required_tests",
        ):
            payload[field] = list(payload[field])
        return payload


@dataclass(frozen=True)
class RemediationPlanSummary:
    source_finding_count: int
    relevant_finding_count: int
    unique_finding_count: int
    classified_finding_count: int
    unclassified_finding_count: int
    multiply_classified_finding_count: int
    manual_review_count: int
    remediation_item_count: int
    branch_package_count: int
    dependency_count: int
    plan_accounting_consistent: bool
    plan_deterministic: bool


@dataclass(frozen=True)
class BranchTemplate:
    branch_id: str
    proposed_branch_name: str
    sequence: int
    actions: tuple[RemediationAction, ...]
    dependencies: tuple[str, ...]
    allowed_file_scope: tuple[str, ...]
    forbidden_file_scope: tuple[str, ...]
    entry_gate: tuple[str, ...]
    exit_gate: tuple[str, ...]
    required_tests: tuple[str, ...]
    expected_boundary_delta: str
    policy_update_allowed: bool


@dataclass(frozen=True)
class RemediationTaxonomy:
    schema_version: str
    taxonomy_id: str
    source_policy_schema_version: str
    source_boundary_report_schema_version: str
    closed_actions: tuple[RemediationAction, ...]
    action_precedence: tuple[RemediationAction, ...]
    finding_action_rules: tuple[tuple[str, RemediationAction], ...]
    operational_namespaces: tuple[str, ...]
    future_branch_templates: tuple[BranchTemplate, ...]
    global_forbidden_capabilities: tuple[str, ...]
    planning_safety_flags: tuple[tuple[str, bool], ...]
    taxonomy_path: str
    taxonomy_sha256: str

    def precedence_for(self, action: RemediationAction) -> int:
        return self.action_precedence.index(action) + 1


Runner = Callable[..., subprocess.CompletedProcess[str]]


def load_remediation_taxonomy(
    *,
    project_root: str | Path,
    taxonomy_path: str | Path = DEFAULT_TAXONOMY,
) -> RemediationTaxonomy:
    root = Path(project_root).resolve()
    path = _safe_project_file(root, taxonomy_path, expected_suffix=".json")
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TaxonomyError(f"taxonomy_unreadable:{type(exc).__name__}") from exc
    if not isinstance(payload, Mapping):
        raise TaxonomyError("taxonomy_root_must_be_object")
    try:
        closed_actions = tuple(RemediationAction(str(item)) for item in payload["closed_actions"])
        precedence = tuple(
            RemediationAction(str(item)) for item in payload["action_precedence"]
        )
        rules_payload = _required_mapping(payload, "finding_action_rules")
        rules = tuple(
            sorted(
                (str(key), RemediationAction(str(value)))
                for key, value in rules_payload.items()
            )
        )
        templates = tuple(
            _parse_branch_template(item)
            for item in _mapping_sequence(payload["future_branch_templates"])
        )
        taxonomy = RemediationTaxonomy(
            schema_version=str(payload["schema_version"]),
            taxonomy_id=str(payload["taxonomy_id"]),
            source_policy_schema_version=str(payload["source_policy_schema_version"]),
            source_boundary_report_schema_version=str(
                payload["source_boundary_report_schema_version"]
            ),
            closed_actions=closed_actions,
            action_precedence=precedence,
            finding_action_rules=rules,
            operational_namespaces=tuple(
                sorted(_normalize_path(str(item)) for item in payload["operational_namespaces"])
            ),
            future_branch_templates=templates,
            global_forbidden_capabilities=tuple(
                sorted(str(item) for item in payload["global_forbidden_capabilities"])
            ),
            planning_safety_flags=tuple(
                sorted(
                    (str(key), bool(value))
                    for key, value in _required_mapping(
                        payload, "planning_safety_flags"
                    ).items()
                )
            ),
            taxonomy_path=_display_path(path, root),
            taxonomy_sha256=hashlib.sha256(raw).hexdigest(),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise TaxonomyError(f"taxonomy_structure_invalid:{type(exc).__name__}") from exc
    return taxonomy


def verify_remediation_taxonomy(taxonomy: RemediationTaxonomy) -> tuple[str, ...]:
    errors: list[str] = []
    all_actions = set(RemediationAction)
    if taxonomy.schema_version != TAXONOMY_SCHEMA_VERSION:
        errors.append("taxonomy_schema_version_invalid")
    if taxonomy.source_policy_schema_version != POLICY_SCHEMA_VERSION:
        errors.append("taxonomy_source_policy_schema_version_invalid")
    if taxonomy.source_boundary_report_schema_version != SOURCE_REPORT_SCHEMA_VERSION:
        errors.append("taxonomy_source_boundary_report_schema_version_invalid")
    if set(taxonomy.closed_actions) != all_actions or len(taxonomy.closed_actions) != len(
        all_actions
    ):
        errors.append("taxonomy_closed_actions_invalid")
    if set(taxonomy.action_precedence) != all_actions or len(
        taxonomy.action_precedence
    ) != len(all_actions):
        errors.append("taxonomy_action_precedence_incomplete")
    rules = dict(taxonomy.finding_action_rules)
    if not RELEVANT_CLASSIFICATIONS <= set(rules):
        errors.append("taxonomy_finding_action_rules_incomplete")
    if any(action not in all_actions for action in rules.values()):
        errors.append("taxonomy_finding_action_rule_unknown_action")
    if not taxonomy.operational_namespaces:
        errors.append("taxonomy_operational_namespaces_missing")
    templates = sorted(taxonomy.future_branch_templates, key=lambda item: item.sequence)
    if tuple(item.proposed_branch_name for item in templates) != REQUIRED_BRANCH_NAMES:
        errors.append("taxonomy_branch_templates_invalid")
    if tuple(item.sequence for item in templates) != (1, 2, 3, 4, 5, 6):
        errors.append("taxonomy_branch_sequence_invalid")
    if len({item.branch_id for item in templates}) != len(templates):
        errors.append("taxonomy_branch_id_duplicate")
    if set(taxonomy.global_forbidden_capabilities) != REQUIRED_GLOBAL_FORBIDDEN_CAPABILITIES:
        errors.append("taxonomy_forbidden_capabilities_invalid")
    safety = dict(taxonomy.planning_safety_flags)
    if set(safety) != set(PLAN_SAFETY_FLAGS) or any(safety.values()):
        errors.append("taxonomy_planning_safety_flags_invalid")
    for template in templates:
        if template.policy_update_allowed and template.sequence not in {5, 6}:
            errors.append(f"taxonomy_policy_update_scope_invalid:{template.branch_id}")
        if any(_forbidden_target(value) for value in template.allowed_file_scope):
            errors.append(f"taxonomy_allowed_scope_unsafe:{template.branch_id}")
    errors.extend(_validate_template_dependencies(templates))
    return tuple(sorted(set(errors)))


def finding_reference_from_payload(payload: Mapping[str, Any]) -> FindingReference:
    classification = str(payload.get("classification", "")).strip()
    relative_path = _normalize_path(str(payload.get("relative_path", "")).strip())
    symbol = _sanitize_token(payload.get("symbol"), fallback="unknown_symbol")
    evidence = _sanitize_token(payload.get("evidence"), fallback="unknown_evidence")
    try:
        line_number = int(payload.get("line_number", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("finding_line_number_invalid") from exc
    if not classification or not relative_path or line_number < 0:
        raise ValueError("finding_required_fields_invalid")
    canonical = {
        "classification": classification,
        "relative_path": relative_path,
        "line_number": line_number,
        "symbol": symbol,
        "evidence_token": evidence,
    }
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return FindingReference(
        source_finding_id=_sanitize_token(payload.get("finding_id"), fallback=digest),
        finding_id=digest,
        classification=classification,
        severity=str(payload.get("severity", "")).casefold(),
        relative_path=relative_path,
        line_number=line_number,
        symbol=symbol,
        evidence_token=evidence,
    )


def classify_finding(
    finding: FindingReference,
    taxonomy: RemediationTaxonomy,
    *,
    structural_proof: StructuralProof | None = None,
) -> RemediationItem:
    classification = finding.classification
    namespace = _namespace_for(finding.relative_path, taxonomy.operational_namespaces)
    operational = _is_operational(finding, taxonomy.operational_namespaces)

    if classification in WRITER_CLASSIFICATIONS:
        action = RemediationAction.REMOVE_LEGACY_WRITER_CALLSITE
        rationale_code = "active_legacy_writer_or_import_callsite"
        rationale = "Remove the active writer/import callsite in a dedicated future branch."
    elif classification == FindingClassification.PROHIBITED_OPERATIONAL_CONSUMER.value:
        action = RemediationAction.ISOLATE_OPERATIONAL_REFERENCE
        rationale_code = "legacy_master_reference_in_operational_namespace"
        rationale = "Isolate the legacy Master reference from operational behavior."
    elif classification == FindingClassification.CONFIGURATION_REFERENCE.value:
        action = RemediationAction.ISOLATE_OPERATIONAL_REFERENCE
        rationale_code = "operational_configuration_points_to_legacy_master"
        rationale = "Remove the legacy Master from operational configuration semantics."
    elif classification == FindingClassification.FINGERPRINT_V2_MISCLASSIFICATION.value:
        action = RemediationAction.BLOCKED_REQUIRES_MANUAL_REVIEW
        rationale_code = "fingerprint_v2_misclassification_requires_contract_review"
        rationale = "Fingerprint V2 misuse has no automatic safe remediation action."
    elif classification == FindingClassification.DYNAMIC_REFERENCE_UNRESOLVED.value:
        if operational:
            action = RemediationAction.ISOLATE_OPERATIONAL_REFERENCE
            rationale_code = "dynamic_reference_in_operational_namespace"
            rationale = "Operational precedence prevents treating this as diagnostic-only."
        else:
            action = RemediationAction.RESOLVE_DYNAMIC_REFERENCE
            rationale_code = "dynamic_reference_requires_ast_resolution"
            rationale = "Resolve the dynamic construction without suppressing the finding."
    elif classification == FindingClassification.UNREGISTERED_MASTER_CONSUMER.value:
        tokens = {finding.symbol.casefold(), finding.evidence_token.casefold()}
        if tokens & INSTITUTIONAL_READER_TOKENS:
            action = RemediationAction.REGISTER_AS_READONLY_RESEARCH_CONSUMER
            rationale_code = "institutional_readonly_consumer_not_registered"
            rationale = "Propose policy registration only after prior remediation gates pass."
        elif finding.evidence_token in READ_EVIDENCE_TOKENS:
            action = RemediationAction.REFACTOR_TO_INSTITUTIONAL_READONLY_ADAPTER
            rationale_code = "direct_legacy_master_read"
            rationale = "Migrate the historical read to read_trader_master_readonly."
        else:
            action = RemediationAction.BLOCKED_REQUIRES_MANUAL_REVIEW
            rationale_code = "unregistered_consumer_evidence_insufficient"
            rationale = "The structured finding does not prove a safe migration or registration."
    elif classification == FindingClassification.LEGACY_WRITER_IMPLEMENTATION.value:
        if finding.relative_path == LEGACY_WRITER_PATH:
            action = RemediationAction.KEEP_QUARANTINED_LEGACY_IMPLEMENTATION
            rationale_code = "legacy_writer_implementation_remains_quarantined"
            rationale = "Keep inventoried with no read, write, import, or execution authority."
        else:
            action = RemediationAction.BLOCKED_REQUIRES_MANUAL_REVIEW
            rationale_code = "unexpected_legacy_writer_implementation"
            rationale = "Only the known legacy implementation may receive quarantine action."
    elif classification in {
        FindingClassification.TEST_FIXTURE_REFERENCE.value,
        FindingClassification.DOCUMENTATION_REFERENCE.value,
    }:
        if structural_proof is not None and structural_proof.proof_reproducible:
            action = RemediationAction.FALSE_POSITIVE_WITH_STRUCTURAL_PROOF
            rationale_code = "non_executable_reference_structurally_proven"
            rationale = "Retain reproducible structural proof for closeout verification."
        else:
            action = RemediationAction.BLOCKED_REQUIRES_MANUAL_REVIEW
            rationale_code = "false_positive_without_structural_proof"
            rationale = "A path or classification alone is not structural proof."
    else:
        action = RemediationAction.BLOCKED_REQUIRES_MANUAL_REVIEW
        rationale_code = "finding_classification_requires_manual_review"
        rationale = "The closed taxonomy cannot safely infer a remediation."

    branch_id = _branch_id_for_action(action)
    code_change = action in {
        RemediationAction.REMOVE_LEGACY_WRITER_CALLSITE,
        RemediationAction.ISOLATE_OPERATIONAL_REFERENCE,
        RemediationAction.RESOLVE_DYNAMIC_REFERENCE,
        RemediationAction.REFACTOR_TO_INSTITUTIONAL_READONLY_ADAPTER,
    }
    policy_change = action == RemediationAction.REGISTER_AS_READONLY_RESEARCH_CONSUMER
    manual = action == RemediationAction.BLOCKED_REQUIRES_MANUAL_REVIEW
    allowed_scope = () if not code_change else (finding.relative_path,)
    if policy_change:
        allowed_scope = (POLICY_RELATIVE_PATH,)
    forbidden_scope = (
        "data/trades/**",
        FINGERPRINT_SPEC_PATH,
        LEGACY_WRITER_PATH if action != RemediationAction.KEEP_QUARANTINED_LEGACY_IMPLEMENTATION else "data/trades/**",
    )
    return RemediationItem(
        finding_id=finding.finding_id,
        finding_classification=classification,
        source_severity=finding.severity,
        relative_path=finding.relative_path,
        line_number=finding.line_number,
        symbol=finding.symbol,
        namespace=namespace,
        source_evidence=finding.evidence_token,
        recommended_action=action,
        action_precedence=taxonomy.precedence_for(action),
        rationale_code=rationale_code,
        rationale=rationale,
        structural_proof=structural_proof,
        requires_code_change=code_change,
        future_policy_change_required=policy_change,
        requires_manual_review=manual,
        proposed_branch_id=branch_id,
        allowed_file_scope=tuple(sorted(set(allowed_scope))),
        forbidden_file_scope=tuple(sorted(set(forbidden_scope))),
        required_tests=_required_tests_for_action(action),
        expected_boundary_delta=_expected_delta_for_action(action),
        safety_flags=tuple(sorted(PLAN_SAFETY_FLAGS.items())),
    )


def build_remediation_plan_report(
    *,
    project_root: str | Path,
    policy_path: str | Path = DEFAULT_POLICY,
    taxonomy_path: str | Path = DEFAULT_TAXONOMY,
    trader_master_path: str | Path = DEFAULT_MASTER,
    write_report: bool = False,
    output_json: str | Path = DEFAULT_JSON_REPORT,
    output_markdown: str | Path = DEFAULT_MARKDOWN_REPORT,
    generated_at_utc: str | None = None,
    source_boundary_report: Mapping[str, Any] | None = None,
    source_commit_sha: str | None = None,
    source_branch: str | None = None,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    json_path = _resolve_without_symlinks(root, output_json)
    markdown_path = _resolve_without_symlinks(root, output_markdown)
    report = _base_report(
        root=root,
        generated_at_utc=generated_at_utc,
        output_json=json_path,
        output_markdown=markdown_path,
        write_report=write_report,
    )
    if write_report:
        output_errors = _validate_output_paths(root, json_path, markdown_path)
        if output_errors:
            return _blocked(report, "unsafe_report_output_path", output_errors)
    try:
        taxonomy = load_remediation_taxonomy(
            project_root=root,
            taxonomy_path=taxonomy_path,
        )
    except TaxonomyError as exc:
        return _blocked(report, "remediation_taxonomy_unreadable", [str(exc)])
    taxonomy_errors = verify_remediation_taxonomy(taxonomy)
    report.update(
        taxonomy_id=taxonomy.taxonomy_id,
        taxonomy_sha256=taxonomy.taxonomy_sha256,
    )
    if taxonomy_errors:
        return _blocked(report, "remediation_taxonomy_invalid", taxonomy_errors)
    try:
        policy = load_legacy_master_policy(
            project_root=root,
            policy_path=policy_path,
        )
    except PolicyError as exc:
        return _blocked(report, "source_policy_unreadable", [str(exc)])
    policy_errors = verify_legacy_master_policy(policy)
    report.update(policy_id=policy.policy_id, policy_sha256=policy.policy_sha256)
    if policy_errors:
        return _blocked(report, "source_policy_invalid", policy_errors)

    boundary = dict(source_boundary_report) if source_boundary_report is not None else (
        build_legacy_master_boundary_report(
            project_root=root,
            policy_path=policy_path,
            trader_master_path=trader_master_path,
            write_report=False,
        )
    )
    commit, branch = _resolve_git_identity(
        root,
        runner=runner,
        source_commit_sha=source_commit_sha,
        source_branch=source_branch,
    )
    report.update(
        source_commit_sha=commit,
        source_branch=branch,
        source_boundary_report_schema_version=boundary.get("schema_version"),
        source_boundary_decision=boundary.get("decision"),
        source_boundary_reason=boundary.get("reason"),
        source_high_count=int(boundary.get("high_count", 0)),
        source_critical_count=int(boundary.get("critical_count", 0)),
        source_dynamic_reference_unresolved_count=int(
            boundary.get("dynamic_reference_unresolved_count", 0)
        ),
        source_consumer_inventory_complete=bool(
            boundary.get("consumer_inventory_complete", False)
        ),
        source_segregation_enforced=bool(boundary.get("segregation_enforced", False)),
    )
    if boundary.get("status") != "ok":
        report.update(
            decision=RemediationDecision.SOURCE_AUDIT_BLOCKED.value,
            source_finding_count=len(_finding_payloads(boundary)),
        )
        return _blocked(
            report,
            "legacy_boundary_source_audit_blocked",
            [str(boundary.get("reason", "source_audit_blocked"))],
        )
    if _source_boundary_is_clean(boundary):
        report.update(
            status="ok",
            reason="legacy_boundary_already_segregated",
            decision=RemediationDecision.NOT_REQUIRED.value,
            source_finding_count=len(_finding_payloads(boundary)),
            plan_accounting_consistent=True,
            plan_deterministic=True,
        )
        return _maybe_write(report, write_report, json_path, markdown_path)

    raw_findings = _finding_payloads(boundary)
    relevant_payloads = [payload for payload in raw_findings if _is_relevant_payload(payload)]
    references, duplicate_conflicts, parse_errors = _normalize_findings(relevant_payloads)
    path_errors = _validate_finding_paths(root, references)
    if path_errors:
        return _blocked(report, "unsafe_finding_path", path_errors)

    items = tuple(
        sorted(
            (classify_finding(finding, taxonomy) for finding in references),
            key=_item_sort_key,
        )
    )
    unclassified_count = len(duplicate_conflicts) + len(parse_errors)
    multiply_classified_count = 0
    accounting_consistent = (
        len(references) + unclassified_count
        == len(items) + unclassified_count
        and multiply_classified_count == 0
        and not duplicate_conflicts
        and not parse_errors
    )
    packages, dependencies, package_errors = build_branch_packages(
        items=items,
        taxonomy=taxonomy,
    )
    manual_items = tuple(item for item in items if item.requires_manual_review)
    plan_errors = tuple(sorted(set((*duplicate_conflicts, *parse_errors, *package_errors))))
    if plan_errors or not accounting_consistent:
        decision = RemediationDecision.PLAN_INCOMPLETE
        reason = "legacy_boundary_remediation_plan_incomplete"
    elif manual_items:
        decision = RemediationDecision.REQUIRES_MANUAL_REVIEW
        reason = "legacy_boundary_remediation_plan_requires_manual_review"
    else:
        decision = RemediationDecision.PLAN_READY
        reason = "legacy_boundary_remediation_plan_ready"

    action_counts = Counter(item.recommended_action.value for item in items)
    target_files = {
        target
        for package in packages
        for target in package.target_files
    }
    summary = RemediationPlanSummary(
        source_finding_count=len(raw_findings),
        relevant_finding_count=len(references) + unclassified_count,
        unique_finding_count=len(references) + len(duplicate_conflicts),
        classified_finding_count=len(items),
        unclassified_finding_count=unclassified_count,
        multiply_classified_finding_count=multiply_classified_count,
        manual_review_count=len(manual_items),
        remediation_item_count=len(items),
        branch_package_count=len(packages),
        dependency_count=len(dependencies),
        plan_accounting_consistent=accounting_consistent and not package_errors,
        plan_deterministic=True,
    )
    report.update(
        status="ok",
        reason=reason,
        decision=decision.value,
        **asdict(summary),
        remediation_items=[item.to_dict() for item in items],
        branch_packages=[item.to_dict() for item in packages],
        dependencies=[asdict(item) for item in dependencies],
        manual_review_queue=[item.to_dict() for item in manual_items],
        action_counts=dict(sorted(action_counts.items())),
        target_file_count=len(target_files),
        planned_code_change_count=sum(item.requires_code_change for item in items),
        planned_policy_change_count=sum(
            item.future_policy_change_required for item in items
        ),
        writer_callsite_removal_count=action_counts[
            RemediationAction.REMOVE_LEGACY_WRITER_CALLSITE.value
        ],
        operational_reference_isolation_count=action_counts[
            RemediationAction.ISOLATE_OPERATIONAL_REFERENCE.value
        ],
        dynamic_reference_resolution_count=action_counts[
            RemediationAction.RESOLVE_DYNAMIC_REFERENCE.value
        ],
        readonly_adapter_migration_count=action_counts[
            RemediationAction.REFACTOR_TO_INSTITUTIONAL_READONLY_ADAPTER.value
        ],
        readonly_consumer_registration_count=action_counts[
            RemediationAction.REGISTER_AS_READONLY_RESEARCH_CONSUMER.value
        ],
        quarantined_implementation_count=action_counts[
            RemediationAction.KEEP_QUARANTINED_LEGACY_IMPLEMENTATION.value
        ],
        false_positive_with_proof_count=action_counts[
            RemediationAction.FALSE_POSITIVE_WITH_STRUCTURAL_PROOF.value
        ],
        validation_errors=list(plan_errors),
        blockers=list(plan_errors),
        **PLAN_SAFETY_FLAGS,
        safety_flags=dict(PLAN_SAFETY_FLAGS),
    )
    return _maybe_write(report, write_report, json_path, markdown_path)


def build_branch_packages(
    *,
    items: Sequence[RemediationItem],
    taxonomy: RemediationTaxonomy,
) -> tuple[
    tuple[BranchRemediationPackage, ...],
    tuple[RemediationDependency, ...],
    tuple[str, ...],
]:
    templates = {
        item.branch_id: item for item in taxonomy.future_branch_templates
    }
    grouped: dict[str, list[RemediationItem]] = {}
    for item in items:
        if item.requires_manual_review:
            continue
        grouped.setdefault(item.proposed_branch_id, []).append(item)

    executable_sequences = [
        templates[branch_id].sequence
        for branch_id in grouped
        if branch_id in templates and branch_id != "reverification_closeout"
    ]
    selected_ids: set[str] = set()
    if executable_sequences:
        maximum = max(executable_sequences)
        selected_ids.update(
            template.branch_id
            for template in taxonomy.future_branch_templates
            if template.sequence <= maximum
        )
    if grouped:
        selected_ids.add("reverification_closeout")
    errors: list[str] = []
    unknown = set(grouped) - set(templates)
    if unknown:
        errors.extend(f"branch_mapping_missing:{item}" for item in sorted(unknown))

    packages: list[BranchRemediationPackage] = []
    for template in sorted(taxonomy.future_branch_templates, key=lambda item: item.sequence):
        if template.branch_id not in selected_ids:
            continue
        branch_items = sorted(grouped.get(template.branch_id, []), key=_item_sort_key)
        target_files = {
            item.relative_path for item in branch_items if item.requires_code_change
        }
        if template.branch_id == "readonly_consumer_registration" and branch_items:
            target_files.add(POLICY_RELATIVE_PATH)
        if template.branch_id == "reverification_closeout":
            target_files.update(
                {
                    POLICY_RELATIVE_PATH,
                    "smartcrypto/data/trader_master_fingerprint_v2/legacy_master_governance.py",
                }
            )
        if any(_forbidden_target(item) for item in target_files):
            errors.append(f"branch_target_scope_forbidden:{template.branch_id}")
        package_dependencies = (
            tuple(
                sorted(
                    selected_ids - {template.branch_id},
                    key=lambda value: templates[value].sequence,
                )
            )
            if template.branch_id == "reverification_closeout"
            else tuple(item for item in template.dependencies if item in selected_ids)
        )
        allowed = set(target_files)
        allowed.update(
            item
            for item in template.allowed_file_scope
            if item != "<finding-target-files>"
        )
        packages.append(
            BranchRemediationPackage(
                branch_id=template.branch_id,
                proposed_branch_name=template.proposed_branch_name,
                sequence=template.sequence,
                actions=tuple(action.value for action in template.actions),
                finding_ids=tuple(item.finding_id for item in branch_items),
                target_files=tuple(sorted(target_files)),
                allowed_file_scope=tuple(sorted(allowed)),
                forbidden_file_scope=template.forbidden_file_scope,
                dependencies=package_dependencies,
                entry_gate=template.entry_gate,
                exit_gate=template.exit_gate,
                required_tests=template.required_tests,
                expected_boundary_delta=template.expected_boundary_delta,
                policy_update_allowed=template.policy_update_allowed,
            )
        )
    dependency_records = tuple(
        RemediationDependency(
            branch_id=package.branch_id,
            depends_on_branch_id=dependency,
            rationale="canonical_remediation_sequence",
        )
        for package in packages
        for dependency in package.dependencies
    )
    if _has_dependency_cycle(packages):
        errors.append("branch_dependency_cycle_detected")
    return tuple(packages), dependency_records, tuple(sorted(set(errors)))


def render_remediation_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Trader Master Legacy Boundary Remediation Plan V1",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Decision: `{report.get('decision')}`",
        f"- Source boundary: `{report.get('source_boundary_decision')}`",
        f"- Relevant findings: `{report.get('relevant_finding_count')}`",
        f"- Manual review: `{report.get('manual_review_count')}`",
        f"- Branch packages: `{report.get('branch_package_count')}`",
        "- Remediation applied: `false`",
        "- Operational authority: `false`",
        "",
        "## Planned Actions",
        "",
        "| Action | Count |",
        "| --- | ---: |",
    ]
    for action, count in sorted(dict(report.get("action_counts", {})).items()):
        lines.append(f"| `{action}` | {count} |")
    lines.extend(["", "## Future Branch Sequence", ""])
    for package in report.get("branch_packages", []):
        lines.append(
            f"{package['sequence']}. `{package['proposed_branch_name']}` "
            f"({len(package['finding_ids'])} findings; implementation authorized: false)"
        )
    lines.extend(
        [
            "",
            "## Institutional Boundary",
            "",
            "This plan is evidence for human review. It does not apply remediation, create branches, update policy, register consumers, remove writers, change operational references, generate Fingerprint V2, import trades, train models, change risk, or send orders.",
            "",
        ]
    )
    return "\n".join(lines)


def _normalize_findings(
    payloads: Sequence[Mapping[str, Any]],
) -> tuple[tuple[FindingReference, ...], tuple[str, ...], tuple[str, ...]]:
    by_source_id: dict[str, FindingReference] = {}
    by_finding_id: dict[str, FindingReference] = {}
    conflicts: set[str] = set()
    parse_errors: list[str] = []
    for index, payload in enumerate(payloads):
        try:
            finding = finding_reference_from_payload(payload)
        except ValueError as exc:
            parse_errors.append(f"finding_parse_error:{index}:{exc}")
            continue
        previous_source = by_source_id.get(finding.source_finding_id)
        if previous_source is not None and (
            previous_source.canonical_payload() != finding.canonical_payload()
        ):
            conflicts.add(f"duplicate_source_finding_id_conflict:{finding.source_finding_id}")
            by_finding_id.pop(previous_source.finding_id, None)
            continue
        previous_finding = by_finding_id.get(finding.finding_id)
        if previous_finding is not None and (
            previous_finding.canonical_payload() != finding.canonical_payload()
        ):
            conflicts.add(f"duplicate_planner_finding_id_conflict:{finding.finding_id}")
            by_finding_id.pop(finding.finding_id, None)
            continue
        by_source_id[finding.source_finding_id] = finding
        by_finding_id[finding.finding_id] = finding
    references = tuple(
        sorted(
            by_finding_id.values(),
            key=lambda item: (
                item.relative_path,
                item.line_number,
                item.classification,
                item.symbol,
                item.finding_id,
            ),
        )
    )
    return references, tuple(sorted(conflicts)), tuple(sorted(parse_errors))


def _validate_finding_paths(root: Path, findings: Sequence[FindingReference]) -> tuple[str, ...]:
    errors: list[str] = []
    for finding in findings:
        candidate = _resolve_without_symlinks(root, finding.relative_path)
        try:
            candidate.relative_to(root)
        except ValueError:
            errors.append(f"finding_path_outside_project:{finding.finding_id}")
            continue
        if candidate.is_symlink():
            errors.append(f"finding_path_symlink_forbidden:{finding.finding_id}")
    return tuple(sorted(set(errors)))


def _source_boundary_is_clean(boundary: Mapping[str, Any]) -> bool:
    return (
        boundary.get("decision") == "LEGACY_MASTER_SEGREGATED_RESEARCH_ONLY"
        and int(boundary.get("high_count", -1)) == 0
        and int(boundary.get("critical_count", -1)) == 0
        and int(boundary.get("dynamic_reference_unresolved_count", -1)) == 0
        and bool(boundary.get("segregation_enforced"))
    )


def _is_relevant_payload(payload: Mapping[str, Any]) -> bool:
    severity = str(payload.get("severity", "")).casefold()
    classification = str(payload.get("classification", ""))
    return severity in {Severity.HIGH.value, Severity.CRITICAL.value} or (
        classification in RELEVANT_CLASSIFICATIONS
    )


def _finding_payloads(boundary: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    value = boundary.get("findings", [])
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _parse_branch_template(payload: Mapping[str, Any]) -> BranchTemplate:
    return BranchTemplate(
        branch_id=str(payload["branch_id"]),
        proposed_branch_name=str(payload["proposed_branch_name"]),
        sequence=int(payload["sequence"]),
        actions=tuple(RemediationAction(str(item)) for item in payload["actions"]),
        dependencies=tuple(str(item) for item in payload["dependencies"]),
        allowed_file_scope=tuple(str(item) for item in payload["allowed_file_scope"]),
        forbidden_file_scope=tuple(str(item) for item in payload["forbidden_file_scope"]),
        entry_gate=tuple(str(item) for item in payload["entry_gate"]),
        exit_gate=tuple(str(item) for item in payload["exit_gate"]),
        required_tests=tuple(str(item) for item in payload["required_tests"]),
        expected_boundary_delta=str(payload["expected_boundary_delta"]),
        policy_update_allowed=bool(payload["policy_update_allowed"]),
    )


def _validate_template_dependencies(templates: Sequence[BranchTemplate]) -> tuple[str, ...]:
    by_id = {item.branch_id: item for item in templates}
    errors: list[str] = []
    for template in templates:
        for dependency in template.dependencies:
            if dependency == "<all-applicable-predecessors>":
                continue
            if dependency not in by_id:
                errors.append(f"taxonomy_branch_dependency_missing:{template.branch_id}")
            elif by_id[dependency].sequence >= template.sequence:
                errors.append(f"taxonomy_branch_dependency_order_invalid:{template.branch_id}")
    return tuple(errors)


def _has_dependency_cycle(packages: Sequence[BranchRemediationPackage]) -> bool:
    graph = {item.branch_id: set(item.dependencies) for item in packages}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(dependency) for dependency in graph.get(node, set())):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in graph)


def _branch_id_for_action(action: RemediationAction) -> str:
    return {
        RemediationAction.REMOVE_LEGACY_WRITER_CALLSITE: "writer_callsite_removal",
        RemediationAction.ISOLATE_OPERATIONAL_REFERENCE: "operational_reference_isolation",
        RemediationAction.RESOLVE_DYNAMIC_REFERENCE: "dynamic_reference_resolution",
        RemediationAction.REFACTOR_TO_INSTITUTIONAL_READONLY_ADAPTER: "readonly_adapter_migration",
        RemediationAction.REGISTER_AS_READONLY_RESEARCH_CONSUMER: "readonly_consumer_registration",
        RemediationAction.KEEP_QUARANTINED_LEGACY_IMPLEMENTATION: "reverification_closeout",
        RemediationAction.FALSE_POSITIVE_WITH_STRUCTURAL_PROOF: "reverification_closeout",
        RemediationAction.BLOCKED_REQUIRES_MANUAL_REVIEW: "manual_review_queue",
    }[action]


def _required_tests_for_action(action: RemediationAction) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                "tests/test_trader_master_legacy_research_only_boundary_v1.py",
                f"focused_{action.value.casefold()}_tests",
            }
        )
    )


def _expected_delta_for_action(action: RemediationAction) -> str:
    return {
        RemediationAction.REMOVE_LEGACY_WRITER_CALLSITE: "writer_or_import_callsite_count_decreases",
        RemediationAction.ISOLATE_OPERATIONAL_REFERENCE: "operational_consumer_count_decreases",
        RemediationAction.RESOLVE_DYNAMIC_REFERENCE: "dynamic_reference_unresolved_count_decreases",
        RemediationAction.REFACTOR_TO_INSTITUTIONAL_READONLY_ADAPTER: "direct_read_count_decreases",
        RemediationAction.REGISTER_AS_READONLY_RESEARCH_CONSUMER: "registered_readonly_consumer_count_increases_after_gate",
        RemediationAction.KEEP_QUARANTINED_LEGACY_IMPLEMENTATION: "quarantine_remains_explicit_and_unauthorized",
        RemediationAction.FALSE_POSITIVE_WITH_STRUCTURAL_PROOF: "finding_closed_with_reproducible_structural_proof",
        RemediationAction.BLOCKED_REQUIRES_MANUAL_REVIEW: "no_change_until_human_classification",
    }[action]


def _namespace_for(path: str, operational_namespaces: Sequence[str]) -> str:
    normalized = _normalize_path(path).casefold()
    for prefix in operational_namespaces:
        if normalized.startswith(prefix.casefold()):
            return prefix.rstrip("/").replace("/", ".")
    if "/" in normalized:
        return normalized.rsplit("/", maxsplit=1)[0].replace("/", ".")
    return "repository_root"


def _is_operational(
    finding: FindingReference,
    operational_namespaces: Sequence[str],
) -> bool:
    normalized = finding.relative_path.casefold()
    return (
        finding.classification
        == FindingClassification.PROHIBITED_OPERATIONAL_CONSUMER.value
        or any(normalized.startswith(item.casefold()) for item in operational_namespaces)
        or any(
            token in normalized
            for token in (
                "signal_producer",
                "scheduler",
                "release",
                "canary",
                "live_",
                "order_execution",
                "paper_selection",
                "risk_decision",
            )
        )
    )


def _item_sort_key(item: RemediationItem) -> tuple[Any, ...]:
    return (
        item.action_precedence,
        item.relative_path,
        item.line_number,
        item.finding_classification,
        item.finding_id,
    )


def _sanitize_token(value: Any, *, fallback: str) -> str:
    text = str(value or "").strip()
    sanitized = re.sub(r"[^A-Za-z0-9_.:\-]", "_", text)[:160]
    return sanitized or fallback


def _forbidden_target(value: str) -> bool:
    normalized = _normalize_path(value).casefold()
    return (
        normalized.startswith("data/trades/")
        or normalized == "data/trades/**"
        or normalized == FINGERPRINT_SPEC_PATH.casefold()
    )


def _safe_project_file(root: Path, value: str | Path, *, expected_suffix: str) -> Path:
    path = _resolve_without_symlinks(root, value)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise TaxonomyError("path_outside_project_root") from exc
    if path.is_symlink():
        raise TaxonomyError("path_symlink_forbidden")
    if not path.is_file():
        raise TaxonomyError("path_missing")
    if path.suffix.casefold() != expected_suffix:
        raise TaxonomyError("path_extension_invalid")
    return path


def _resolve_without_symlinks(root: Path, value: str | Path) -> Path:
    path = Path(value)
    candidate = path if path.is_absolute() else root / path
    return Path(os.path.abspath(candidate))


def _validate_output_paths(root: Path, *paths: Path) -> tuple[str, ...]:
    allowed = (root / "data" / "reports").resolve()
    errors: list[str] = []
    for path in paths:
        try:
            path.resolve().relative_to(allowed)
        except ValueError:
            errors.append(f"report_path_outside_data_reports:{path}")
        if path.suffix.casefold() not in {".json", ".md"}:
            errors.append(f"report_extension_invalid:{path}")
    return tuple(sorted(set(errors)))


def _resolve_git_identity(
    root: Path,
    *,
    runner: Runner,
    source_commit_sha: str | None,
    source_branch: str | None,
) -> tuple[str, str]:
    commit = source_commit_sha
    branch = source_branch
    if commit is None:
        commit = _git_value(root, ["git", "rev-parse", "HEAD"], runner=runner)
    if branch is None:
        branch = _git_value(
            root,
            ["git", "branch", "--show-current"],
            runner=runner,
        )
    return commit or "unknown", branch or "unknown"


def _git_value(root: Path, argv: list[str], *, runner: Runner) -> str:
    try:
        completed = runner(  # nosec B603 - fixed Git argv, no shell, bounded timeout
            argv,
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            shell=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def _base_report(
    *,
    root: Path,
    generated_at_utc: str | None,
    output_json: Path,
    output_markdown: Path,
    write_report: bool,
) -> dict[str, Any]:
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "generated_at_utc": generated_at_utc or datetime.now(UTC).isoformat(),
        "status": "blocked",
        "reason": "not_evaluated",
        "decision": RemediationDecision.SOURCE_AUDIT_BLOCKED.value,
        "source_commit_sha": None,
        "source_branch": None,
        "taxonomy_id": None,
        "taxonomy_sha256": None,
        "policy_id": None,
        "policy_sha256": None,
        "source_boundary_report_schema_version": None,
        "source_boundary_decision": None,
        "source_boundary_reason": None,
        "source_high_count": 0,
        "source_critical_count": 0,
        "source_dynamic_reference_unresolved_count": 0,
        "source_consumer_inventory_complete": False,
        "source_segregation_enforced": False,
        "source_finding_count": 0,
        "relevant_finding_count": 0,
        "unique_finding_count": 0,
        "classified_finding_count": 0,
        "unclassified_finding_count": 0,
        "multiply_classified_finding_count": 0,
        "manual_review_count": 0,
        "remediation_item_count": 0,
        "branch_package_count": 0,
        "dependency_count": 0,
        "remediation_items": [],
        "branch_packages": [],
        "dependencies": [],
        "manual_review_queue": [],
        "action_counts": {},
        "target_file_count": 0,
        "planned_code_change_count": 0,
        "planned_policy_change_count": 0,
        "writer_callsite_removal_count": 0,
        "operational_reference_isolation_count": 0,
        "dynamic_reference_resolution_count": 0,
        "readonly_adapter_migration_count": 0,
        "readonly_consumer_registration_count": 0,
        "quarantined_implementation_count": 0,
        "false_positive_with_proof_count": 0,
        "plan_accounting_consistent": False,
        "plan_deterministic": False,
        "write_requested": bool(write_report),
        "write_performed": False,
        "output_paths": {
            "json": _display_path(output_json, root),
            "markdown": _display_path(output_markdown, root),
        },
        "validation_errors": [],
        "blockers": [],
        **PLAN_SAFETY_FLAGS,
        "safety_flags": dict(PLAN_SAFETY_FLAGS),
    }


def _blocked(
    report: Mapping[str, Any],
    reason: str,
    errors: Sequence[str],
) -> dict[str, Any]:
    final = dict(report)
    final.update(
        status="blocked",
        reason=reason,
        decision=RemediationDecision.SOURCE_AUDIT_BLOCKED.value,
        validation_errors=sorted(set(errors)),
        blockers=sorted(set(errors)),
        write_performed=False,
        **PLAN_SAFETY_FLAGS,
        safety_flags=dict(PLAN_SAFETY_FLAGS),
    )
    return final


def _maybe_write(
    report: dict[str, Any],
    write_report: bool,
    json_path: Path,
    markdown_path: Path,
) -> dict[str, Any]:
    if not write_report:
        return report
    final = dict(report)
    final["write_performed"] = True
    _atomic_write(
        json_path,
        json.dumps(final, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
    )
    _atomic_write(markdown_path, render_remediation_markdown(final))
    return final


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _required_mapping(payload: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    value = payload[field]
    if not isinstance(value, Mapping):
        raise TypeError(f"{field}_must_be_mapping")
    return value


def _mapping_sequence(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TypeError("expected_mapping_sequence")
    if not all(isinstance(item, Mapping) for item in value):
        raise TypeError("expected_mapping_sequence")
    return tuple(value)


def _normalize_path(value: str) -> str:
    return value.replace("\\", "/").removeprefix("./")


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return "<OUTSIDE_PROJECT_ROOT>"


__all__ = [
    "BranchRemediationPackage",
    "FindingReference",
    "PLAN_SAFETY_FLAGS",
    "RemediationAction",
    "RemediationDecision",
    "RemediationDependency",
    "RemediationItem",
    "RemediationPlanSummary",
    "RemediationTaxonomy",
    "StructuralProof",
    "TaxonomyError",
    "build_branch_packages",
    "build_remediation_plan_report",
    "classify_finding",
    "finding_reference_from_payload",
    "load_remediation_taxonomy",
    "render_remediation_markdown",
    "verify_remediation_taxonomy",
]
