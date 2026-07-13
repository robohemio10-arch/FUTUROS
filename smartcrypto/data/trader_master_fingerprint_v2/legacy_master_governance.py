"""Governance boundary for the legacy non-Fingerprint-V2 Trader Master."""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import subprocess  # nosec B404 - fixed local Git command, shell disabled, bounded timeout
import tokenize
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from .fingerprint_spec import HEX_SHA256
from .master_adapter import read_trader_master_readonly


POLICY_SCHEMA_VERSION = "trader_master_legacy_research_only_policy_v1"
REPORT_SCHEMA_VERSION = "trader_master_legacy_research_only_boundary_report_v1"
DATASET_CLASSIFICATION = "research_only_legacy_non_v2"
DEFAULT_POLICY = Path("config/trader_master_legacy_research_only_policy_v1.json")
DEFAULT_MASTER = Path("data/trades/trades_master.parquet")
DEFAULT_JSON_REPORT = Path("data/reports/trader_master_legacy_research_only_boundary_v1.json")
DEFAULT_MARKDOWN_REPORT = Path("data/reports/trader_master_legacy_research_only_boundary_v1.md")
MASTER_REFERENCE = "data/trades/trades_master.parquet"

REQUIRED_ALLOWED_PURPOSES = frozenset(
    {
        "historical_readonly_research",
        "lineage_diagnostics",
        "evidence_inventory",
        "non_operational_strategy_research",
        "read_only_data_quality_analysis",
    }
)
REQUIRED_REASSESSMENT_TRIGGERS = frozenset(
    {
        "new_authoritative_joinable_evidence",
        "sanitized_resolution_of_blocked_artifacts",
        "versioned_source_contract_approved",
        "authoritative_account_scope_attestation",
        "authoritative_instrument_contract_recovered",
        "authoritative_financial_decomposition_recovered",
    }
)
RESTRICTED_POLICY_FLAGS = (
    "fingerprint_v2_compatible",
    "authoritative_for_identity",
    "authoritative_for_deduplication",
    "authoritative_for_financial_decomposition",
    "bridge_authorized",
    "import_authorized",
    "write_authorized",
    "operational_training_authorized",
    "paper_signal_selection_authorized",
    "live_signal_selection_authorized",
    "risk_decision_authorized",
    "order_execution_authorized",
    "operational_authority",
)
SAFE_READ_CAPABILITIES = frozenset(
    {"read_rows", "read_schema", "compute_hash", "diagnostic_metrics"}
)
OPERATIONAL_PATH_TOKENS = (
    "smartcrypto/execution/",
    "smartcrypto/risk/",
    "smartcrypto/runtime/",
    "smartcrypto/learning/",
    "smartcrypto/qlib_engine/",
    "smartcrypto/dashboard/",
    "freqtrade/",
)
CONFIG_EXTENSIONS = frozenset({".yaml", ".yml", ".json", ".toml"})

SAFETY_FLAGS: dict[str, bool] = {
    "bridge_authorized": False,
    "import_authorized": False,
    "fingerprint_generation_allowed": False,
    "write_authorized": False,
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


class AccessMode(StrEnum):
    READ_ONLY = "read_only"
    WRITE = "write"
    IMPORT = "import"
    FINGERPRINT_GENERATION = "fingerprint_generation"
    DEDUPLICATION = "deduplication"
    OPERATIONAL_TRAINING = "operational_training"
    PAPER_SIGNAL_SELECTION = "paper_signal_selection"
    LIVE_SIGNAL_SELECTION = "live_signal_selection"
    RISK_DECISION = "risk_decision"
    ORDER_EXECUTION = "order_execution"


class AccessDecision(StrEnum):
    ALLOW_READONLY_RESEARCH = "ALLOW_READONLY_RESEARCH"
    DENY_UNREGISTERED_CONSUMER = "DENY_UNREGISTERED_CONSUMER"
    DENY_PURPOSE_NOT_ALLOWED = "DENY_PURPOSE_NOT_ALLOWED"
    DENY_WRITE_CAPABILITY = "DENY_WRITE_CAPABILITY"
    DENY_FINGERPRINT_V2_USE = "DENY_FINGERPRINT_V2_USE"
    DENY_DEDUPLICATION_USE = "DENY_DEDUPLICATION_USE"
    DENY_IMPORT_USE = "DENY_IMPORT_USE"
    DENY_OPERATIONAL_TRAINING_USE = "DENY_OPERATIONAL_TRAINING_USE"
    DENY_PAPER_SIGNAL_USE = "DENY_PAPER_SIGNAL_USE"
    DENY_LIVE_SIGNAL_USE = "DENY_LIVE_SIGNAL_USE"
    DENY_RISK_USE = "DENY_RISK_USE"
    DENY_ORDER_EXECUTION_USE = "DENY_ORDER_EXECUTION_USE"
    DENY_POLICY_INVALID = "DENY_POLICY_INVALID"
    DENY_DATASET_DRIFT = "DENY_DATASET_DRIFT"


class FindingClassification(StrEnum):
    REGISTERED_READONLY_CONSUMER = "registered_readonly_consumer"
    UNREGISTERED_MASTER_CONSUMER = "unregistered_master_consumer"
    PROHIBITED_OPERATIONAL_CONSUMER = "prohibited_operational_consumer"
    LEGACY_WRITER_IMPLEMENTATION = "legacy_writer_implementation"
    LEGACY_WRITER_CALLSITE = "legacy_writer_callsite"
    DIRECT_MASTER_WRITE = "direct_master_write"
    DIRECT_MASTER_IMPORT = "direct_master_import"
    FINGERPRINT_V2_MISCLASSIFICATION = "fingerprint_v2_misclassification"
    DYNAMIC_REFERENCE_UNRESOLVED = "dynamic_reference_unresolved"
    CONFIGURATION_REFERENCE = "configuration_reference"
    TEST_FIXTURE_REFERENCE = "test_fixture_reference"
    DOCUMENTATION_REFERENCE = "documentation_reference"


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PolicyError(ValueError):
    """Raised when a policy cannot be read or structurally parsed."""


class TrackedFileDiscoveryError(RuntimeError):
    """Raised when tracked-file discovery cannot complete."""


@dataclass(frozen=True)
class ConsumerRegistration:
    relative_path: str
    consumer_classification: str
    allowed_purposes: tuple[str, ...]
    allowed_access_mode: str
    allowed_capabilities: tuple[str, ...]
    justification: str
    operational_authority: bool


@dataclass(frozen=True)
class LegacyMasterPolicy:
    schema_version: str
    policy_id: str
    dataset_id: str
    relative_path: str
    dataset_classification: str
    expected_sha256: str
    expected_size_bytes: int
    expected_row_count: int
    expected_schema_columns: tuple[str, ...]
    restricted_flags: tuple[tuple[str, bool], ...]
    evidence_basis: tuple[tuple[str, Any], ...]
    allowed_purposes: tuple[str, ...]
    prohibited_capabilities: tuple[str, ...]
    registered_consumers: tuple[ConsumerRegistration, ...]
    quarantined_legacy_implementations: tuple[dict[str, Any], ...]
    reassessment_triggers: tuple[str, ...]
    policy_path: str
    policy_sha256: str

    def flag(self, name: str) -> bool:
        return dict(self.restricted_flags).get(name, False)


@dataclass(frozen=True)
class DatasetEvidence:
    status: str
    reason: str
    trader_master_path: str
    expected_sha256: str
    observed_sha256_before: str | None
    observed_sha256_after: str | None
    hash_preserved: bool
    expected_size_bytes: int
    observed_size_before: int | None
    observed_size_after: int | None
    size_preserved: bool
    expected_row_count: int
    observed_row_count: int
    expected_schema_columns: tuple[str, ...]
    observed_schema_columns: tuple[str, ...]
    temp_copy_used: bool
    artifact_contract_matches: bool
    validation_errors: tuple[str, ...]


@dataclass(frozen=True)
class AccessRequest:
    consumer_path: str
    purpose: str
    access_mode: AccessMode
    requested_capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class AccessEvaluation:
    decision: AccessDecision
    allowed: bool
    reason: str
    consumer_path: str
    purpose: str
    access_mode: str
    requested_capabilities: tuple[str, ...]
    operational_authority: bool = False
    import_eligible: bool = False
    fingerprint_generation_allowed: bool = False
    writes_trader_master: bool = False


@dataclass(frozen=True)
class AuditFinding:
    finding_id: str
    classification: FindingClassification
    severity: Severity
    relative_path: str
    line_number: int
    symbol: str
    evidence: str
    remediation: str
    operational_authority: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["classification"] = self.classification.value
        payload["severity"] = self.severity.value
        return payload


@dataclass(frozen=True)
class TrackedFileInventory:
    paths: tuple[str, ...]
    discovery_mode: str
    complete: bool


@dataclass(frozen=True)
class _ReferenceSignal:
    line_number: int
    symbol: str
    operation: str
    evidence: str


Runner = Callable[..., subprocess.CompletedProcess[str]]


def load_legacy_master_policy(
    *, project_root: str | Path, policy_path: str | Path = DEFAULT_POLICY
) -> LegacyMasterPolicy:
    root = Path(project_root).resolve()
    path = _resolve(root, policy_path)
    error = _validate_project_file(root, path, expected_suffix=".json")
    if error:
        raise PolicyError(error)
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PolicyError(f"policy_unreadable:{type(exc).__name__}") from exc
    if not isinstance(payload, Mapping):
        raise PolicyError("policy_root_must_be_object")
    try:
        registrations = tuple(
            _parse_registration(item)
            for item in _mapping_sequence(payload.get("registered_consumers"))
        )
        quarantined = tuple(
            dict(item)
            for item in _mapping_sequence(payload.get("quarantined_legacy_implementations"))
        )
        evidence_basis = tuple(sorted(dict(_required_mapping(payload, "evidence_basis")).items()))
        policy = LegacyMasterPolicy(
            schema_version=str(payload["schema_version"]),
            policy_id=str(payload["policy_id"]),
            dataset_id=str(payload["dataset_id"]),
            relative_path=_normalize_path(str(payload["relative_path"])),
            dataset_classification=str(payload["dataset_classification"]),
            expected_sha256=str(payload["expected_sha256"]).casefold(),
            expected_size_bytes=int(payload["expected_size_bytes"]),
            expected_row_count=int(payload["expected_row_count"]),
            expected_schema_columns=tuple(str(item) for item in payload["expected_schema_columns"]),
            restricted_flags=tuple(
                (name, bool(payload[name])) for name in RESTRICTED_POLICY_FLAGS
            ),
            evidence_basis=evidence_basis,
            allowed_purposes=tuple(str(item) for item in payload["allowed_purposes"]),
            prohibited_capabilities=tuple(
                str(item) for item in payload["prohibited_capabilities"]
            ),
            registered_consumers=registrations,
            quarantined_legacy_implementations=quarantined,
            reassessment_triggers=tuple(
                str(item) for item in payload["reassessment_triggers"]
            ),
            policy_path=_display_path(path, root),
            policy_sha256=hashlib.sha256(raw).hexdigest(),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PolicyError(f"policy_structure_invalid:{type(exc).__name__}") from exc
    return policy


def verify_legacy_master_policy(policy: LegacyMasterPolicy) -> tuple[str, ...]:
    errors: list[str] = []
    if policy.schema_version != POLICY_SCHEMA_VERSION:
        errors.append("policy_schema_version_invalid")
    if not policy.policy_id or not policy.dataset_id:
        errors.append("policy_identity_missing")
    if policy.relative_path != MASTER_REFERENCE:
        errors.append("policy_dataset_path_invalid")
    if policy.dataset_classification != DATASET_CLASSIFICATION:
        errors.append("policy_dataset_classification_invalid")
    if HEX_SHA256.fullmatch(policy.expected_sha256) is None:
        errors.append("policy_expected_sha256_invalid")
    if policy.expected_size_bytes <= 0 or policy.expected_row_count <= 0:
        errors.append("policy_expected_artifact_metrics_invalid")
    if not policy.expected_schema_columns or len(set(policy.expected_schema_columns)) != len(
        policy.expected_schema_columns
    ):
        errors.append("policy_expected_schema_invalid")
    for name, value in policy.restricted_flags:
        if value:
            errors.append(f"policy_restricted_flag_true:{name}")
    if set(policy.allowed_purposes) != REQUIRED_ALLOWED_PURPOSES:
        errors.append("policy_allowed_purposes_invalid")
    if not set(AccessMode) - {AccessMode.READ_ONLY} <= {
        AccessMode(item)
        for item in policy.prohibited_capabilities
        if item in {mode.value for mode in AccessMode}
    }:
        errors.append("policy_prohibited_capabilities_incomplete")
    paths = [item.relative_path for item in policy.registered_consumers]
    if len(paths) != len(set(paths)):
        errors.append("policy_registered_consumer_duplicate")
    for registration in policy.registered_consumers:
        if (
            registration.consumer_classification != "registered_readonly_consumer"
            or registration.allowed_access_mode != AccessMode.READ_ONLY.value
            or registration.operational_authority
            or not set(registration.allowed_purposes) <= REQUIRED_ALLOWED_PURPOSES
            or not set(registration.allowed_capabilities) <= SAFE_READ_CAPABILITIES
        ):
            errors.append(f"policy_registered_consumer_unsafe:{registration.relative_path}")
    quarantined_paths = {
        _normalize_path(str(item.get("relative_path", "")))
        for item in policy.quarantined_legacy_implementations
    }
    if "smartcrypto/data/trades_importer.py" not in quarantined_paths:
        errors.append("policy_legacy_writer_inventory_missing")
    if set(policy.reassessment_triggers) != REQUIRED_REASSESSMENT_TRIGGERS:
        errors.append("policy_reassessment_triggers_invalid")
    basis = dict(policy.evidence_basis)
    required_basis = {
        "reconciliation_decision": "BLOCKED_BY_UNVERIFIABLE_MASTER_ROWS",
        "trader_master_row_count": 3058,
        "master_valid_fingerprint_row_count": 0,
        "lineage_decision": "EXTERNAL_AUTHORITATIVE_EVIDENCE_REQUIRED",
        "blocked_by_multiple_lineage_gaps_count": 3058,
        "fingerprint_generation_allowed_count": 0,
        "evidence_inventory_decision": "NO_AUTHORITATIVE_EVIDENCE_FOUND",
        "evidence_decision_scope": "safely_inspected_artifacts_only",
        "inventory_coverage_complete": False,
        "authoritative_evidence_absence_proven": False,
        "blocked_artifacts_may_contain_unassessed_evidence": True,
        "bridge_design_preconditions_satisfied": False,
    }
    for key, expected in required_basis.items():
        if basis.get(key) != expected:
            errors.append(f"policy_evidence_basis_invalid:{key}")
    for forbidden in (
        "irrecoverable",
        "evidence_absence_proven",
        "permanently_unverifiable",
        "bridge_impossible_forever",
    ):
        if basis.get(forbidden) is True:
            errors.append(f"policy_irreversible_claim_forbidden:{forbidden}")
    return tuple(sorted(set(errors)))


def verify_pinned_legacy_master_artifact(
    *,
    project_root: str | Path,
    policy: LegacyMasterPolicy,
    trader_master_path: str | Path | None = None,
) -> DatasetEvidence:
    root = Path(project_root).resolve()
    requested = trader_master_path or policy.relative_path
    requested_path = _resolve(root, requested)
    policy_path = _resolve(root, policy.relative_path)
    if requested_path != policy_path:
        return DatasetEvidence(
            status="blocked",
            reason="trader_master_path_policy_mismatch",
            trader_master_path=_display_path(requested_path, root),
            expected_sha256=policy.expected_sha256,
            observed_sha256_before=None,
            observed_sha256_after=None,
            hash_preserved=False,
            expected_size_bytes=policy.expected_size_bytes,
            observed_size_before=None,
            observed_size_after=None,
            size_preserved=False,
            expected_row_count=policy.expected_row_count,
            observed_row_count=0,
            expected_schema_columns=policy.expected_schema_columns,
            observed_schema_columns=(),
            temp_copy_used=False,
            artifact_contract_matches=False,
            validation_errors=("trader_master_path_policy_mismatch",),
        )
    bundle = read_trader_master_readonly(
        project_root=root,
        trader_master_path=requested_path,
    )
    report = bundle.report
    observed_before = _optional_text(report.get("trader_master_sha256_before"))
    observed_after = _optional_text(report.get("trader_master_sha256_after"))
    size_before = _optional_int(report.get("trader_master_size_before"))
    size_after = _optional_int(report.get("trader_master_size_after"))
    row_count = int(report.get("trader_master_row_count", 0))
    schema = tuple(str(item) for item in report.get("trader_master_schema_columns", []))
    errors: list[str] = []
    if report.get("status") != "ok":
        errors.append(str(report.get("reason", "trader_master_read_failed")))
    if observed_before != policy.expected_sha256 or observed_after != policy.expected_sha256:
        errors.append("trader_master_sha256_drift")
    if size_before != policy.expected_size_bytes or size_after != policy.expected_size_bytes:
        errors.append("trader_master_size_drift")
    if row_count != policy.expected_row_count:
        errors.append("trader_master_row_count_drift")
    if schema != policy.expected_schema_columns:
        errors.append("trader_master_schema_drift")
    hash_preserved = bool(report.get("trader_master_hash_preserved"))
    size_preserved = size_before is not None and size_before == size_after
    if not hash_preserved:
        errors.append("trader_master_hash_not_preserved")
    if not size_preserved:
        errors.append("trader_master_size_not_preserved")
    contract_matches = not errors
    read_status = str(report.get("status"))
    status = "blocked" if read_status != "ok" else "ok"
    reason = (
        str(report.get("reason", "trader_master_read_failed"))
        if status == "blocked"
        else "legacy_master_artifact_matches_policy"
        if contract_matches
        else "legacy_master_artifact_drift_detected"
    )
    return DatasetEvidence(
        status=status,
        reason=reason,
        trader_master_path=str(report.get("trader_master_path", requested)),
        expected_sha256=policy.expected_sha256,
        observed_sha256_before=observed_before,
        observed_sha256_after=observed_after,
        hash_preserved=hash_preserved,
        expected_size_bytes=policy.expected_size_bytes,
        observed_size_before=size_before,
        observed_size_after=size_after,
        size_preserved=size_preserved,
        expected_row_count=policy.expected_row_count,
        observed_row_count=row_count,
        expected_schema_columns=policy.expected_schema_columns,
        observed_schema_columns=schema,
        temp_copy_used=bool(report.get("trader_master_temp_copy_used")),
        artifact_contract_matches=contract_matches,
        validation_errors=tuple(sorted(set(errors))),
    )


def evaluate_legacy_master_access(
    *,
    policy: LegacyMasterPolicy,
    dataset_evidence: DatasetEvidence,
    request: AccessRequest,
) -> AccessEvaluation:
    normalized_path = _normalize_path(request.consumer_path)
    policy_errors = verify_legacy_master_policy(policy)
    if policy_errors:
        return _access_result(request, AccessDecision.DENY_POLICY_INVALID, "policy_invalid")
    if not dataset_evidence.artifact_contract_matches:
        return _access_result(request, AccessDecision.DENY_DATASET_DRIFT, "dataset_drift")
    registrations = {item.relative_path: item for item in policy.registered_consumers}
    registration = registrations.get(normalized_path)
    if registration is None:
        return _access_result(
            request,
            AccessDecision.DENY_UNREGISTERED_CONSUMER,
            "consumer_not_registered",
        )
    if request.purpose not in policy.allowed_purposes or request.purpose not in registration.allowed_purposes:
        return _access_result(
            request,
            AccessDecision.DENY_PURPOSE_NOT_ALLOWED,
            "purpose_not_allowed",
        )
    denied = _denial_for_mode(request.access_mode)
    if denied is not None:
        return _access_result(request, denied, f"access_mode_denied:{request.access_mode.value}")
    for capability in request.requested_capabilities:
        capability_mode = _mode_from_capability(capability)
        if capability_mode is not None and capability_mode != AccessMode.READ_ONLY:
            return _access_result(
                request,
                _denial_for_mode(capability_mode) or AccessDecision.DENY_WRITE_CAPABILITY,
                f"capability_denied:{capability}",
            )
        if capability not in registration.allowed_capabilities:
            return _access_result(
                request,
                AccessDecision.DENY_WRITE_CAPABILITY,
                f"capability_not_registered:{capability}",
            )
    return _access_result(
        request,
        AccessDecision.ALLOW_READONLY_RESEARCH,
        "registered_readonly_research_allowed",
        allowed=True,
    )


def discover_tracked_files(
    project_root: Path,
    *,
    runner: Runner = subprocess.run,
    timeout_seconds: float = 15.0,
) -> TrackedFileInventory:
    try:
        completed = runner(  # nosec B603 - fixed Git argv, no shell, bounded timeout
            ["git", "ls-files", "-z"],
            cwd=project_root,
            capture_output=True,
            check=False,
            text=True,
            shell=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise TrackedFileDiscoveryError("git_ls_files_timeout") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise TrackedFileDiscoveryError(
            f"git_ls_files_unavailable:{type(exc).__name__}"
        ) from exc
    if completed.returncode != 0:
        raise TrackedFileDiscoveryError("git_ls_files_failed")
    paths = tuple(
        sorted(
            {
                _normalize_path(item)
                for item in completed.stdout.split("\0")
                if item.strip()
            }
        )
    )
    return TrackedFileInventory(paths=paths, discovery_mode="git_ls_files", complete=True)


def analyze_python_source(
    relative_path: str,
    source: str,
    *,
    registered_paths: frozenset[str] | None = None,
) -> tuple[AuditFinding, ...]:
    normalized_path = _normalize_path(relative_path)
    try:
        tree = ast.parse(source, filename=normalized_path)
    except SyntaxError:
        return (
            _finding(
                FindingClassification.DYNAMIC_REFERENCE_UNRESOLVED,
                Severity.MEDIUM,
                normalized_path,
                0,
                "ast_parse",
                "python_source_unparseable",
            ),
        )
    visitor = _MasterReferenceVisitor(source, tree)
    visitor.visit(tree)
    signals = list(visitor.signals)
    if not signals and _lexical_dynamic_reference(source, tree):
        signals.append(
            _ReferenceSignal(
                line_number=_first_reference_line(source),
                symbol="lexical_fallback",
                operation="dynamic_reference",
                evidence="unresolved_lexical_master_reference",
            )
        )
    if _is_test_path(normalized_path):
        return tuple(
            _finding(
                FindingClassification.TEST_FIXTURE_REFERENCE,
                Severity.INFO,
                normalized_path,
                signal.line_number,
                signal.symbol,
                signal.evidence,
            )
            for signal in _deduplicate_signals(signals)
        )
    return tuple(
        _classify_signal(
            normalized_path,
            signal,
            registered_paths=registered_paths or frozenset(),
        )
        for signal in _deduplicate_signals(signals)
    )


def audit_legacy_master_consumers(
    *,
    project_root: str | Path,
    policy: LegacyMasterPolicy,
    tracked_inventory: TrackedFileInventory | None = None,
    runner: Runner = subprocess.run,
    timeout_seconds: float = 15.0,
) -> tuple[tuple[AuditFinding, ...], dict[str, Any]]:
    root = Path(project_root).resolve()
    inventory = tracked_inventory or discover_tracked_files(
        root,
        runner=runner,
        timeout_seconds=timeout_seconds,
    )
    findings: list[AuditFinding] = []
    python_count = 0
    configuration_count = 0
    ignored = {
        _normalize_path(policy.policy_path),
        "PROJECT_MANIFEST_CLEAN.json",
    }
    registered_paths = frozenset(
        _normalize_path(item.relative_path) for item in policy.registered_consumers
    )
    for relative in inventory.paths:
        path = root / relative
        suffix = path.suffix.casefold()
        if relative in ignored or relative.startswith("docs/") or not path.is_file():
            continue
        if suffix == ".py":
            python_count += 1
            try:
                source = path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeError):
                findings.append(
                    _finding(
                        FindingClassification.DYNAMIC_REFERENCE_UNRESOLVED,
                        Severity.MEDIUM,
                        relative,
                        0,
                        "read_source",
                        "tracked_python_source_unreadable",
                    )
                )
                continue
            findings.extend(
                analyze_python_source(
                    relative,
                    source,
                    registered_paths=registered_paths,
                )
            )
        elif suffix in CONFIG_EXTENSIONS:
            configuration_count += 1
            try:
                text = path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeError):
                findings.append(
                    _finding(
                        FindingClassification.DYNAMIC_REFERENCE_UNRESOLVED,
                        Severity.MEDIUM,
                        relative,
                        0,
                        "read_configuration",
                        "tracked_configuration_unreadable",
                    )
                )
                continue
            if _contains_master_reference(text):
                severity = Severity.CRITICAL if _is_operational_path(relative) else Severity.HIGH
                findings.append(
                    _finding(
                        FindingClassification.CONFIGURATION_REFERENCE,
                        severity,
                        relative,
                        _first_reference_line(text),
                        "configuration_reference",
                        "configuration_points_to_legacy_master",
                    )
                )
    for item in policy.quarantined_legacy_implementations:
        relative = _normalize_path(str(item.get("relative_path", "")))
        if relative and relative in inventory.paths:
            findings.append(
                _finding(
                    FindingClassification.LEGACY_WRITER_IMPLEMENTATION,
                    Severity.INFO,
                    relative,
                    0,
                    "quarantined_legacy_implementation",
                    "legacy_writer_capability_inventoried_not_authorized",
                )
            )
    deduplicated = _deduplicate_findings(findings)
    metadata = {
        "tracked_file_discovery_mode": inventory.discovery_mode,
        "tracked_file_count": len(inventory.paths),
        "scanned_python_file_count": python_count,
        "scanned_configuration_file_count": configuration_count,
        "tracked_file_discovery_complete": inventory.complete,
    }
    return deduplicated, metadata


def build_legacy_master_boundary_report(
    *,
    project_root: str | Path,
    policy_path: str | Path = DEFAULT_POLICY,
    trader_master_path: str | Path = DEFAULT_MASTER,
    write_report: bool = False,
    output_json: str | Path = DEFAULT_JSON_REPORT,
    output_markdown: str | Path = DEFAULT_MARKDOWN_REPORT,
    generated_at_utc: str | None = None,
    runner: Runner = subprocess.run,
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    json_path = _resolve(root, output_json)
    markdown_path = _resolve(root, output_markdown)
    report = _base_report(
        root=root,
        policy_path=policy_path,
        trader_master_path=trader_master_path,
        output_json=json_path,
        output_markdown=markdown_path,
        write_report=write_report,
        generated_at_utc=generated_at_utc,
    )
    if write_report:
        output_errors = _validate_output_paths(root, json_path, markdown_path)
        if output_errors:
            return _blocked(report, "unsafe_report_output_path", output_errors)
    try:
        policy = load_legacy_master_policy(project_root=root, policy_path=policy_path)
    except PolicyError as exc:
        return _blocked(report, "legacy_master_policy_unreadable", [str(exc)])
    policy_errors = verify_legacy_master_policy(policy)
    report.update(
        policy_id=policy.policy_id,
        policy_sha256=policy.policy_sha256,
        dataset_id=policy.dataset_id,
        dataset_classification=policy.dataset_classification,
        registered_consumer_count=len(policy.registered_consumers),
        reassessment_allowed=True,
        reassessment_triggers=list(policy.reassessment_triggers),
    )
    if policy_errors:
        report.update(
            status="ok",
            reason="legacy_master_policy_invalid",
            decision="LEGACY_MASTER_POLICY_INVALID",
            validation_errors=list(policy_errors),
            blockers=list(policy_errors),
        )
        return _maybe_write(report, write_report, json_path, markdown_path)
    evidence = verify_pinned_legacy_master_artifact(
        project_root=root,
        policy=policy,
        trader_master_path=trader_master_path,
    )
    report.update(_dataset_report_fields(evidence))
    if evidence.status == "blocked":
        return _blocked(report, evidence.reason, evidence.validation_errors)
    if not evidence.artifact_contract_matches:
        report.update(
            status="ok",
            reason="legacy_master_artifact_drift_detected",
            decision="LEGACY_MASTER_ARTIFACT_DRIFT_DETECTED",
            validation_errors=list(evidence.validation_errors),
            blockers=list(evidence.validation_errors),
        )
        return _maybe_write(report, write_report, json_path, markdown_path)
    try:
        findings, discovery = audit_legacy_master_consumers(
            project_root=root,
            policy=policy,
            runner=runner,
            timeout_seconds=timeout_seconds,
        )
    except TrackedFileDiscoveryError as exc:
        return _blocked(report, "tracked_file_discovery_failed", [str(exc)])
    report.update(discovery)
    counts = Counter(item.classification.value for item in findings)
    severities = Counter(item.severity.value for item in findings)
    observed_consumers = {
        item.relative_path
        for item in findings
        if item.classification
        not in {
            FindingClassification.TEST_FIXTURE_REFERENCE,
            FindingClassification.DOCUMENTATION_REFERENCE,
            FindingClassification.LEGACY_WRITER_IMPLEMENTATION,
        }
    }
    consumer_inventory_complete = bool(discovery["tracked_file_discovery_complete"]) and not counts[
        FindingClassification.DYNAMIC_REFERENCE_UNRESOLVED.value
    ]
    high_or_critical = severities[Severity.HIGH.value] + severities[Severity.CRITICAL.value]
    if high_or_critical:
        decision = "LEGACY_MASTER_BOUNDARY_VIOLATED"
    elif not consumer_inventory_complete:
        decision = "LEGACY_MASTER_CONSUMER_INVENTORY_INCOMPLETE"
    else:
        decision = "LEGACY_MASTER_SEGREGATED_RESEARCH_ONLY"
    report.update(
        status="ok",
        reason=_decision_reason(decision),
        decision=decision,
        observed_consumer_count=len(observed_consumers),
        unregistered_consumer_count=len(
            {
                item.relative_path
                for item in findings
                if item.classification == FindingClassification.UNREGISTERED_MASTER_CONSUMER
            }
        ),
        operational_consumer_count=len(
            {
                item.relative_path
                for item in findings
                if item.classification == FindingClassification.PROHIBITED_OPERATIONAL_CONSUMER
            }
        ),
        legacy_writer_implementation_count=counts[
            FindingClassification.LEGACY_WRITER_IMPLEMENTATION.value
        ],
        legacy_writer_callsite_count=counts[FindingClassification.LEGACY_WRITER_CALLSITE.value],
        direct_write_count=counts[FindingClassification.DIRECT_MASTER_WRITE.value],
        direct_import_count=counts[FindingClassification.DIRECT_MASTER_IMPORT.value],
        dynamic_reference_unresolved_count=counts[
            FindingClassification.DYNAMIC_REFERENCE_UNRESOLVED.value
        ],
        consumer_inventory_complete=consumer_inventory_complete,
        findings=[item.to_dict() for item in findings],
        high_count=severities[Severity.HIGH.value],
        critical_count=severities[Severity.CRITICAL.value],
        segregation_enforced=decision == "LEGACY_MASTER_SEGREGATED_RESEARCH_ONLY",
        validation_errors=[],
        blockers=[],
        **SAFETY_FLAGS,
        safety_flags=dict(SAFETY_FLAGS),
    )
    return _maybe_write(report, write_report, json_path, markdown_path)


def render_markdown(report: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# Trader Master Legacy Research-Only Boundary V1",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Decision: `{report.get('decision')}`",
            f"- Dataset classification: `{report.get('dataset_classification')}`",
            f"- Artifact contract matches: `{report.get('artifact_contract_matches')}`",
            f"- Consumer inventory complete: `{report.get('consumer_inventory_complete')}`",
            f"- Registered consumers: `{report.get('registered_consumer_count')}`",
            f"- Unregistered consumers: `{report.get('unregistered_consumer_count')}`",
            f"- Operational consumers: `{report.get('operational_consumer_count')}`",
            f"- Legacy writer callsites: `{report.get('legacy_writer_callsite_count')}`",
            f"- High findings: `{report.get('high_count')}`",
            f"- Critical findings: `{report.get('critical_count')}`",
            f"- Segregation enforced: `{report.get('segregation_enforced')}`",
            "",
            "## Boundary",
            "",
            "The legacy Master remains physically unchanged and has no identity, deduplication, training, risk, signal, import, or execution authority.",
            "",
            "Reassessment requires a new policy version, authoritative joinable evidence, explicit authorization, hash review, and complete tests.",
            "",
        ]
    )


class _MasterReferenceVisitor(ast.NodeVisitor):
    def __init__(self, source: str, tree: ast.AST) -> None:
        self.source = source
        self.signals: list[_ReferenceSignal] = []
        self.importer_aliases: set[str] = set()
        self.imported_writer_aliases: dict[str, str] = {}
        self.docstring_nodes = _docstring_node_ids(tree)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "smartcrypto.data.trades_importer":
                self.importer_aliases.add(alias.asname or alias.name.split(".")[-1])
                self._add(node, "trades_importer", "import_call", "direct_trades_importer_import")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "smartcrypto.data.trades_importer":
            for alias in node.names:
                local = alias.asname or alias.name
                if alias.name in {"write_master", "import_trades_incrementally"}:
                    self.imported_writer_aliases[local] = (
                        "import_call"
                        if alias.name == "import_trades_incrementally"
                        else "writer_call"
                    )
                self._add(node, local, "import_call", "direct_trades_importer_import")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if id(node) not in self.docstring_nodes and isinstance(node.value, str):
            if _contains_master_reference(node.value):
                self._add(node, "string_literal", "read_reference", "literal_master_reference")
        self.generic_visit(node)

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        segment = ast.get_source_segment(self.source, node) or ""
        if _looks_like_dynamic_master_reference(segment):
            self._add(node, "f_string", "dynamic_reference", "dynamic_master_reference")
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        segment = ast.get_source_segment(self.source, node) or ""
        if _looks_like_dynamic_master_reference(segment):
            self._add(node, "binary_expression", "dynamic_reference", "dynamic_master_reference")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node.func)
        short_name = name.rsplit(".", maxsplit=1)[-1]
        arguments_reference_master = any(
            _node_references_master(argument, self.source)
            for argument in (*node.args, *[keyword.value for keyword in node.keywords])
        )
        importer_call = any(name.startswith(f"{alias}.") for alias in self.importer_aliases)
        if short_name in self.imported_writer_aliases or importer_call:
            operation = self.imported_writer_aliases.get(short_name, "writer_call")
            self._add(node, name, operation, "legacy_trades_importer_callsite")
        elif short_name == "import_trades_incrementally":
            self._add(node, name, "import_call", "incremental_import_callsite")
        elif short_name == "write_master":
            self._add(node, name, "writer_call", "legacy_master_writer_callsite")
        elif short_name == "to_parquet" and arguments_reference_master:
            self._add(node, name, "direct_write", "to_parquet_targets_legacy_master")
        elif short_name in {"open", "unlink", "replace", "rename", "move"} and arguments_reference_master:
            self._add(node, name, "direct_write", "filesystem_write_targets_legacy_master")
        elif short_name in {"read_parquet", "read_master"} and arguments_reference_master:
            self._add(node, name, "read_reference", "reader_targets_legacy_master")
        elif short_name == "Path" and arguments_reference_master:
            self._add(node, name, "read_reference", "path_targets_legacy_master")
        if "fingerprint" in name.casefold() and arguments_reference_master:
            self._add(node, name, "fingerprint_use", "legacy_master_used_as_fingerprint_v2")
        self.generic_visit(node)

    def _add(self, node: ast.AST, symbol: str, operation: str, evidence: str) -> None:
        self.signals.append(
            _ReferenceSignal(
                line_number=int(getattr(node, "lineno", 0)),
                symbol=symbol,
                operation=operation,
                evidence=evidence,
            )
        )


def _classify_signal(
    relative_path: str,
    signal: _ReferenceSignal,
    *,
    registered_paths: frozenset[str],
) -> AuditFinding:
    if signal.operation == "direct_write":
        classification = FindingClassification.DIRECT_MASTER_WRITE
        severity = Severity.CRITICAL
    elif signal.operation == "import_call":
        classification = FindingClassification.DIRECT_MASTER_IMPORT
        severity = Severity.CRITICAL
    elif signal.operation == "writer_call":
        classification = FindingClassification.LEGACY_WRITER_CALLSITE
        severity = Severity.CRITICAL
    elif signal.operation == "fingerprint_use":
        classification = FindingClassification.FINGERPRINT_V2_MISCLASSIFICATION
        severity = Severity.CRITICAL
    elif signal.operation == "dynamic_reference":
        classification = FindingClassification.DYNAMIC_REFERENCE_UNRESOLVED
        severity = Severity.MEDIUM
    elif _is_operational_path(relative_path):
        classification = FindingClassification.PROHIBITED_OPERATIONAL_CONSUMER
        severity = Severity.CRITICAL
    elif relative_path in registered_paths:
        classification = FindingClassification.REGISTERED_READONLY_CONSUMER
        severity = Severity.INFO
    else:
        classification = FindingClassification.UNREGISTERED_MASTER_CONSUMER
        severity = Severity.HIGH
    return _finding(
        classification,
        severity,
        relative_path,
        signal.line_number,
        signal.symbol,
        signal.evidence,
    )


def _finding(
    classification: FindingClassification,
    severity: Severity,
    relative_path: str,
    line_number: int,
    symbol: str,
    evidence: str,
) -> AuditFinding:
    material = f"{classification.value}|{relative_path}|{line_number}|{symbol}|{evidence}"
    return AuditFinding(
        finding_id=hashlib.sha256(material.encode("utf-8")).hexdigest()[:24],
        classification=classification,
        severity=severity,
        relative_path=_normalize_path(relative_path),
        line_number=line_number,
        symbol=symbol,
        evidence=evidence,
        remediation="review_consumer_in_dedicated_legacy_master_boundary_remediation_branch",
    )


def _access_result(
    request: AccessRequest,
    decision: AccessDecision,
    reason: str,
    *,
    allowed: bool = False,
) -> AccessEvaluation:
    return AccessEvaluation(
        decision=decision,
        allowed=allowed,
        reason=reason,
        consumer_path=_normalize_path(request.consumer_path),
        purpose=request.purpose,
        access_mode=request.access_mode.value,
        requested_capabilities=request.requested_capabilities,
    )


def _denial_for_mode(mode: AccessMode) -> AccessDecision | None:
    return {
        AccessMode.READ_ONLY: None,
        AccessMode.WRITE: AccessDecision.DENY_WRITE_CAPABILITY,
        AccessMode.FINGERPRINT_GENERATION: AccessDecision.DENY_FINGERPRINT_V2_USE,
        AccessMode.DEDUPLICATION: AccessDecision.DENY_DEDUPLICATION_USE,
        AccessMode.IMPORT: AccessDecision.DENY_IMPORT_USE,
        AccessMode.OPERATIONAL_TRAINING: AccessDecision.DENY_OPERATIONAL_TRAINING_USE,
        AccessMode.PAPER_SIGNAL_SELECTION: AccessDecision.DENY_PAPER_SIGNAL_USE,
        AccessMode.LIVE_SIGNAL_SELECTION: AccessDecision.DENY_LIVE_SIGNAL_USE,
        AccessMode.RISK_DECISION: AccessDecision.DENY_RISK_USE,
        AccessMode.ORDER_EXECUTION: AccessDecision.DENY_ORDER_EXECUTION_USE,
    }[mode]


def _mode_from_capability(value: str) -> AccessMode | None:
    try:
        return AccessMode(value)
    except ValueError:
        return None


def _parse_registration(payload: Mapping[str, Any]) -> ConsumerRegistration:
    return ConsumerRegistration(
        relative_path=_normalize_path(str(payload["relative_path"])),
        consumer_classification=str(payload["consumer_classification"]),
        allowed_purposes=tuple(str(item) for item in payload["allowed_purposes"]),
        allowed_access_mode=str(payload["allowed_access_mode"]),
        allowed_capabilities=tuple(str(item) for item in payload["allowed_capabilities"]),
        justification=str(payload["justification"]),
        operational_authority=bool(payload["operational_authority"]),
    )


def _mapping_sequence(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TypeError("expected_mapping_sequence")
    if not all(isinstance(item, Mapping) for item in value):
        raise TypeError("expected_mapping_sequence")
    return tuple(value)


def _required_mapping(payload: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    value = payload[field]
    if not isinstance(value, Mapping):
        raise TypeError(f"{field}_must_be_mapping")
    return value


def _dataset_report_fields(evidence: DatasetEvidence) -> dict[str, Any]:
    payload = asdict(evidence)
    payload.pop("status", None)
    payload.pop("reason", None)
    payload["expected_schema_columns"] = list(evidence.expected_schema_columns)
    payload["observed_schema_columns"] = list(evidence.observed_schema_columns)
    payload["validation_errors"] = list(evidence.validation_errors)
    return payload


def _deduplicate_signals(signals: Sequence[_ReferenceSignal]) -> tuple[_ReferenceSignal, ...]:
    unique = {
        (item.line_number, item.symbol, item.operation, item.evidence): item
        for item in signals
    }
    return tuple(unique[key] for key in sorted(unique))


def _deduplicate_findings(findings: Sequence[AuditFinding]) -> tuple[AuditFinding, ...]:
    unique = {item.finding_id: item for item in findings}
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                item.relative_path,
                item.line_number,
                item.classification.value,
                item.symbol,
            ),
        )
    )


def _docstring_node_ids(tree: ast.AST) -> set[int]:
    result: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.body and isinstance(node.body[0], ast.Expr):
                value = node.body[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    result.add(id(value))
    return result


def _lexical_dynamic_reference(source: str, tree: ast.AST) -> bool:
    docstring_lines: set[int] = set()
    docstring_node_ids = _docstring_node_ids(tree)
    for node in ast.walk(tree):
        if id(node) in docstring_node_ids:
            start = int(getattr(node, "lineno", 0))
            end = int(getattr(node, "end_lineno", start))
            docstring_lines.update(range(start, end + 1))
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        visible = " ".join(
            token.string
            for token in tokens
            if token.type not in {tokenize.COMMENT, tokenize.ENCODING}
            and token.start[0] not in docstring_lines
        )
    except (IndentationError, tokenize.TokenError):
        return True
    return _looks_like_dynamic_master_reference(visible)


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return "<dynamic_call>"


def _node_references_master(node: ast.AST, source: str) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return _contains_master_reference(node.value)
    segment = ast.get_source_segment(source, node) or ""
    return _contains_master_reference(segment) or _looks_like_dynamic_master_reference(segment)


def _contains_master_reference(value: str) -> bool:
    normalized = value.replace("\\", "/").casefold()
    return MASTER_REFERENCE in normalized or "trades_master.parquet" in normalized


def _looks_like_dynamic_master_reference(value: str) -> bool:
    normalized = value.casefold()
    return "trades_master" in normalized and "parquet" in normalized and not _contains_master_reference(value)


def _is_test_path(path: str) -> bool:
    return path.startswith("tests/")


def _is_operational_path(path: str) -> bool:
    normalized = _normalize_path(path).casefold()
    return any(token in normalized for token in OPERATIONAL_PATH_TOKENS) or any(
        token in normalized
        for token in ("order", "release", "live", "strategy", "signal_producer")
    )


def _first_reference_line(text: str) -> int:
    return next(
        (
            index
            for index, line in enumerate(text.splitlines(), start=1)
            if _contains_master_reference(line)
        ),
        0,
    )


def _decision_reason(decision: str) -> str:
    return {
        "LEGACY_MASTER_SEGREGATED_RESEARCH_ONLY": "legacy_master_boundary_compliant",
        "LEGACY_MASTER_BOUNDARY_VIOLATED": "legacy_master_boundary_violations_detected",
        "LEGACY_MASTER_ARTIFACT_DRIFT_DETECTED": "legacy_master_artifact_drift_detected",
        "LEGACY_MASTER_POLICY_INVALID": "legacy_master_policy_invalid",
        "LEGACY_MASTER_CONSUMER_INVENTORY_INCOMPLETE": "legacy_master_consumer_inventory_incomplete",
    }[decision]


def _base_report(
    *,
    root: Path,
    policy_path: str | Path,
    trader_master_path: str | Path,
    output_json: Path,
    output_markdown: Path,
    write_report: bool,
    generated_at_utc: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at_utc": generated_at_utc or datetime.now(UTC).isoformat(),
        "status": "blocked",
        "reason": "not_evaluated",
        "decision": "LEGACY_MASTER_POLICY_INVALID",
        "policy_path": _display_path(_resolve(root, policy_path), root),
        "policy_id": None,
        "policy_sha256": None,
        "dataset_id": None,
        "dataset_classification": DATASET_CLASSIFICATION,
        "trader_master_path": _display_path(_resolve(root, trader_master_path), root),
        "expected_sha256": None,
        "observed_sha256_before": None,
        "observed_sha256_after": None,
        "hash_preserved": False,
        "expected_size_bytes": None,
        "observed_size_before": None,
        "observed_size_after": None,
        "size_preserved": False,
        "expected_row_count": None,
        "observed_row_count": 0,
        "expected_schema_columns": [],
        "observed_schema_columns": [],
        "temp_copy_used": False,
        "artifact_contract_matches": False,
        "tracked_file_discovery_mode": None,
        "tracked_file_count": 0,
        "scanned_python_file_count": 0,
        "scanned_configuration_file_count": 0,
        "registered_consumer_count": 0,
        "observed_consumer_count": 0,
        "unregistered_consumer_count": 0,
        "operational_consumer_count": 0,
        "legacy_writer_implementation_count": 0,
        "legacy_writer_callsite_count": 0,
        "direct_write_count": 0,
        "direct_import_count": 0,
        "dynamic_reference_unresolved_count": 0,
        "consumer_inventory_complete": False,
        "findings": [],
        "high_count": 0,
        "critical_count": 0,
        "segregation_enforced": False,
        "reassessment_allowed": True,
        "reassessment_triggers": [],
        "evidence_decision_scope": "safely_inspected_artifacts_only",
        "authoritative_evidence_absence_proven": False,
        "write_requested": bool(write_report),
        "write_performed": False,
        "output_paths": {
            "json": _display_path(output_json, root),
            "markdown": _display_path(output_markdown, root),
        },
        "validation_errors": [],
        "blockers": [],
        **SAFETY_FLAGS,
        "safety_flags": dict(SAFETY_FLAGS),
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
        validation_errors=sorted(set(errors)),
        blockers=sorted(set(errors)),
        write_performed=False,
        **SAFETY_FLAGS,
        safety_flags=dict(SAFETY_FLAGS),
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
    _atomic_write(markdown_path, render_markdown(final))
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


def _validate_output_paths(root: Path, *paths: Path) -> tuple[str, ...]:
    allowed = (root / "data/reports").resolve()
    errors: list[str] = []
    for path in paths:
        try:
            path.resolve().relative_to(allowed)
        except ValueError:
            errors.append(f"report_path_outside_data_reports:{path}")
        if path.suffix.casefold() not in {".json", ".md"}:
            errors.append(f"report_extension_invalid:{path}")
    return tuple(sorted(set(errors)))


def _validate_project_file(root: Path, path: Path, *, expected_suffix: str) -> str | None:
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return "path_outside_project_root"
    if path.is_symlink():
        return "path_symlink_forbidden"
    if not path.exists() or not path.is_file():
        return "path_missing"
    if path.suffix.casefold() != expected_suffix:
        return "path_extension_invalid"
    return None


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    candidate = path if path.is_absolute() else root / path
    return Path(os.path.abspath(candidate))


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return "<OUTSIDE_PROJECT_ROOT>"


def _normalize_path(value: str) -> str:
    return value.replace("\\", "/").removeprefix("./")


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


__all__ = [
    "AccessDecision",
    "AccessEvaluation",
    "AccessMode",
    "AccessRequest",
    "AuditFinding",
    "ConsumerRegistration",
    "DatasetEvidence",
    "FindingClassification",
    "LegacyMasterPolicy",
    "PolicyError",
    "Severity",
    "TrackedFileDiscoveryError",
    "TrackedFileInventory",
    "analyze_python_source",
    "audit_legacy_master_consumers",
    "build_legacy_master_boundary_report",
    "discover_tracked_files",
    "evaluate_legacy_master_access",
    "load_legacy_master_policy",
    "render_markdown",
    "verify_legacy_master_policy",
    "verify_pinned_legacy_master_artifact",
]
