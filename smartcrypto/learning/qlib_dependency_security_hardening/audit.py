\
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

DEFAULT_POLICY_PATH = Path("config/qlib_dependency_security_policy_v1.json")
DEFAULT_REPORT_PATH = Path("data/reports/qlib_dependency_security_hardening_v1.json")
EXPECTED_SCHEMA_VERSION = "qlib_dependency_security_policy_v1"
EXPECTED_DECISION = "MANTER_EM_RESEARCH"
PIN_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+(?:\[[^]]+\])?==[^;\s]+$")
SAFETY_DEFAULTS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "operational_authority": False,
    "runtime_updated": False,
    "models_changed": False,
    "model_promotion_performed": False,
    "changes_risk": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "live_trading_enabled": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "canary_release_allowed": False,
    "live_release_allowed": False,
}
REQUIRED_SEMANTIC_INVARIANTS = {
    "resolver_success_does_not_imply_security_gate_pass",
    "single_package_fixed_does_not_imply_dependency_graph_secure",
    "missing_evidence_is_blocking",
    "unknown_finding_is_blocking",
    "dependency_downgrade_requires_full_graph_audit",
    "no_approved_security_clean_resolution_is_blocking",
}


class PolicyError(ValueError):
    """Raised when the versioned dependency-security policy is invalid."""


def _canonical_text_bytes(data: bytes) -> bytes:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise PolicyError("text_input_must_be_utf8") from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_text_sha256(data: bytes) -> str:
    return _sha256_bytes(_canonical_text_bytes(data))


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise PolicyError(f"unreadable_file:{path}") from exc


def _read_json(path: Path) -> Any:
    try:
        return json.loads(_read_bytes(path).decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PolicyError(f"invalid_json:{path}") from exc


def _as_mapping(value: Any, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PolicyError(f"{name}_must_be_object")
    return value


def _as_sequence(value: Any, *, name: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise PolicyError(f"{name}_must_be_array")
    return value


def load_policy(path: Path) -> dict[str, Any]:
    payload = _read_json(path)
    policy = dict(_as_mapping(payload, name="policy"))
    _validate_policy(policy)
    return policy


def _validate_policy(policy: Mapping[str, Any]) -> None:
    if policy.get("schema_version") != EXPECTED_SCHEMA_VERSION:
        raise PolicyError("unexpected_schema_version")
    if policy.get("decision") != EXPECTED_DECISION:
        raise PolicyError("unexpected_decision")
    if policy.get("policy_status") != "active_fail_closed":
        raise PolicyError("policy_not_fail_closed")
    if policy.get("approved_security_clean_resolution_found") is not False:
        raise PolicyError("current_policy_must_not_approve_security_clean_resolution")
    if policy.get("qlib_security_gate_passed") is not False:
        raise PolicyError("current_policy_must_keep_qlib_security_gate_blocked")

    requirements_file = policy.get("requirements_file")
    if not isinstance(requirements_file, str) or requirements_file != "requirements-qlib.lock":
        raise PolicyError("unexpected_requirements_file")

    expected_sha256 = policy.get("expected_requirements_sha256")
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise PolicyError("invalid_expected_requirements_sha256")

    direct_requirements = _as_sequence(
        policy.get("expected_direct_requirements"),
        name="expected_direct_requirements",
    )
    if list(direct_requirements) != ["pyqlib==0.9.7"]:
        raise PolicyError("unexpected_direct_requirements_contract")

    invariants = _as_mapping(policy.get("semantic_invariants"), name="semantic_invariants")
    for key in REQUIRED_SEMANTIC_INVARIANTS:
        if invariants.get(key) is not True:
            raise PolicyError(f"semantic_invariant_not_enforced:{key}")

    safety = _as_mapping(policy.get("safety"), name="safety")
    for key, expected in SAFETY_DEFAULTS.items():
        if safety.get(key) is not expected:
            raise PolicyError(f"unsafe_policy_flag:{key}")

    evidence = _as_mapping(policy.get("certified_evidence"), name="certified_evidence")
    current = _as_mapping(evidence.get("current_resolution"), name="current_resolution")
    fallback = _as_mapping(evidence.get("cryptography_50_fallback"), name="cryptography_50_fallback")
    incompatible = _as_mapping(evidence.get("modern_mlflow_crypto50"), name="modern_mlflow_crypto50")

    if current.get("resolver_status") != "resolved":
        raise PolicyError("current_resolution_not_resolved")
    if current.get("security_status") != "blocked":
        raise PolicyError("current_resolution_not_blocked")
    if int(current.get("known_vulnerability_count", 0)) < 1:
        raise PolicyError("current_resolution_missing_vulnerability")
    _require_finding(current, package="cryptography", version="49.0.0", finding_id="PYSEC-2026-3552")

    if fallback.get("resolver_status") != "resolved":
        raise PolicyError("cryptography_50_fallback_not_resolved")
    if fallback.get("security_status") != "blocked":
        raise PolicyError("cryptography_50_fallback_not_blocked")
    if int(fallback.get("known_vulnerability_count", 0)) < 26:
        raise PolicyError("cryptography_50_fallback_vulnerability_count_too_low")
    summary = _as_mapping(fallback.get("finding_summary"), name="fallback_finding_summary")
    if int(summary.get("mlflow", 0)) < 25:
        raise PolicyError("mlflow_fallback_findings_missing")
    if int(summary.get("pyarrow", 0)) < 1:
        raise PolicyError("pyarrow_fallback_findings_missing")
    _require_finding(
        {"findings": fallback.get("required_findings", [])},
        package="pyarrow",
        version="21.0.0",
        finding_id="PYSEC-2026-113",
    )

    if incompatible.get("resolver_status") != "incompatible":
        raise PolicyError("modern_mlflow_crypto50_must_be_incompatible")
    if incompatible.get("security_status") != "blocked":
        raise PolicyError("modern_mlflow_crypto50_not_blocked")


def _require_finding(
    container: Mapping[str, Any],
    *,
    package: str,
    version: str,
    finding_id: str,
) -> None:
    findings = _as_sequence(container.get("findings"), name="findings")
    for item in findings:
        if not isinstance(item, Mapping):
            continue
        if (
            str(item.get("package", "")).lower() == package.lower()
            and str(item.get("version", "")) == version
            and str(item.get("id", "")) == finding_id
        ):
            return
    raise PolicyError(f"required_finding_missing:{package}:{version}:{finding_id}")


def _logical_requirement_lines(text: str) -> list[str]:
    result: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.split(" #", maxsplit=1)[0].strip()
        if line:
            result.append(line)
    return result


def _audit_lock(project_root: Path, policy: Mapping[str, Any]) -> dict[str, Any]:
    relative = Path(str(policy["requirements_file"]))
    lock_path = project_root / relative
    if not lock_path.is_file():
        return {
            "status": "blocked",
            "reason": "requirements_qlib_lock_missing",
            "path": relative.as_posix(),
            "sha256": None,
            "expected_sha256": policy["expected_requirements_sha256"],
            "hash_matches_policy": False,
            "active_requirements": [],
            "direct_requirements_match_policy": False,
        }

    raw = _read_bytes(lock_path)
    try:
        canonical = _canonical_text_bytes(raw)
        digest = _sha256_bytes(canonical)
        text = canonical.decode("utf-8")
    except PolicyError:
        digest = None
        return {
            "status": "blocked",
            "reason": "requirements_qlib_lock_invalid_encoding",
            "path": relative.as_posix(),
            "sha256": digest,
            "expected_sha256": policy["expected_requirements_sha256"],
            "hash_matches_policy": False,
            "active_requirements": [],
            "direct_requirements_match_policy": False,
        }

    active = _logical_requirement_lines(text)
    all_pinned = all(PIN_PATTERN.fullmatch(line) for line in active)
    expected = list(policy["expected_direct_requirements"])
    hash_matches = digest == policy["expected_requirements_sha256"]
    direct_matches = active == expected and all_pinned
    status = "ok" if hash_matches and direct_matches else "blocked"
    reason = "qlib_lock_contract_ok" if status == "ok" else "qlib_lock_contract_mismatch"
    return {
        "status": status,
        "reason": reason,
        "path": relative.as_posix(),
        "sha256": digest,
        "expected_sha256": policy["expected_requirements_sha256"],
        "hash_matches_policy": hash_matches,
        "active_requirements": active,
        "direct_requirements_match_policy": direct_matches,
    }


def _packages_from_pip_report(payload: Any) -> dict[str, str]:
    root = _as_mapping(payload, name="resolver_report")
    install = _as_sequence(root.get("install"), name="resolver_install")
    packages: dict[str, str] = {}
    for item in install:
        if not isinstance(item, Mapping):
            continue
        metadata = item.get("metadata")
        if not isinstance(metadata, Mapping):
            continue
        name = str(metadata.get("name", "")).strip().lower().replace("_", "-")
        version = str(metadata.get("version", "")).strip()
        if name and version:
            packages[name] = version
    return dict(sorted(packages.items()))


def _normalize_pip_audit_findings(payload: Any) -> list[dict[str, Any]]:
    dependencies: Sequence[Any]
    if isinstance(payload, Mapping):
        if "dependencies" in payload:
            dependencies = _as_sequence(payload["dependencies"], name="pip_audit_dependencies")
        elif "vulnerabilities" in payload:
            dependencies = _as_sequence(payload["vulnerabilities"], name="pip_audit_vulnerabilities")
        else:
            raise PolicyError("unsupported_pip_audit_json_shape")
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        dependencies = payload
    else:
        raise PolicyError("unsupported_pip_audit_json_shape")

    findings: list[dict[str, Any]] = []
    for dep in dependencies:
        if not isinstance(dep, Mapping):
            continue
        dep_name = str(dep.get("name", dep.get("package", ""))).strip()
        dep_version = str(dep.get("version", "")).strip()
        vulns = dep.get("vulns", dep.get("vulnerabilities"))
        if isinstance(vulns, Sequence) and not isinstance(vulns, (str, bytes, bytearray)):
            for vuln in vulns:
                if not isinstance(vuln, Mapping):
                    continue
                findings.append(
                    {
                        "package": dep_name,
                        "version": dep_version,
                        "id": str(vuln.get("id", vuln.get("name", "UNKNOWN"))),
                        "fix_versions": list(vuln.get("fix_versions", []))
                        if isinstance(vuln.get("fix_versions", []), Sequence)
                        and not isinstance(vuln.get("fix_versions", []), (str, bytes, bytearray))
                        else [],
                    }
                )
        elif dep.get("id") or dep.get("vuln_id"):
            findings.append(
                {
                    "package": dep_name,
                    "version": dep_version,
                    "id": str(dep.get("id", dep.get("vuln_id", "UNKNOWN"))),
                    "fix_versions": list(dep.get("fix_versions", []))
                    if isinstance(dep.get("fix_versions", []), Sequence)
                    and not isinstance(dep.get("fix_versions", []), (str, bytes, bytearray))
                    else [],
                }
            )
    return sorted(findings, key=lambda item: (item["package"], item["version"], item["id"]))


def _audit_external_evidence(
    resolver_report: Path | None,
    pip_audit_report: Path | None,
) -> dict[str, Any]:
    if resolver_report is None and pip_audit_report is None:
        return {
            "provided": False,
            "status": "not_provided",
            "resolver_packages": {},
            "finding_count": None,
            "findings": [],
            "security_clean": False,
        }

    if resolver_report is None or pip_audit_report is None:
        return {
            "provided": True,
            "status": "blocked",
            "reason": "incomplete_external_evidence_pair",
            "resolver_packages": {},
            "finding_count": None,
            "findings": [],
            "security_clean": False,
        }

    try:
        resolver_packages = _packages_from_pip_report(_read_json(resolver_report))
        findings = _normalize_pip_audit_findings(_read_json(pip_audit_report))
    except PolicyError as exc:
        return {
            "provided": True,
            "status": "blocked",
            "reason": str(exc),
            "resolver_packages": {},
            "finding_count": None,
            "findings": [],
            "security_clean": False,
        }

    security_clean = bool(resolver_packages) and not findings
    return {
        "provided": True,
        "status": "ok" if security_clean else "blocked",
        "reason": "external_evidence_security_clean" if security_clean else "external_evidence_contains_findings",
        "resolver_packages": resolver_packages,
        "finding_count": len(findings),
        "findings": findings,
        "security_clean": security_clean,
    }


def audit_project(
    project_root: Path,
    *,
    policy_path: Path | None = None,
    resolver_report: Path | None = None,
    pip_audit_report: Path | None = None,
) -> dict[str, Any]:
    root = project_root.resolve()
    selected_policy = policy_path or (root / DEFAULT_POLICY_PATH)
    if not selected_policy.is_absolute():
        selected_policy = root / selected_policy

    policy_hash: str | None = None
    policy: dict[str, Any] | None = None
    policy_error: str | None = None
    try:
        raw_policy = _read_bytes(selected_policy)
        canonical_policy = _canonical_text_bytes(raw_policy)
        policy_hash = _sha256_bytes(canonical_policy)
        policy = json.loads(canonical_policy.decode("utf-8"))
        policy = dict(_as_mapping(policy, name="policy"))
        _validate_policy(policy)
    except (PolicyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        policy_error = str(exc)

    if policy is None:
        return {
            "schema_version": "qlib_dependency_security_audit_v1",
            "status": "blocked",
            "reason": "policy_invalid_or_incomplete",
            "decision": EXPECTED_DECISION,
            "policy_path": selected_policy.as_posix(),
            "policy_sha256": policy_hash,
            "policy_error": policy_error,
            "approved_security_clean_resolution_found": False,
            "qlib_security_gate_passed": False,
            "lock": {
                "status": "blocked",
                "reason": "policy_unavailable",
            },
            "external_evidence": {
                "provided": False,
                "status": "not_evaluated",
                "security_clean": False,
            },
            **SAFETY_DEFAULTS,
        }

    lock = _audit_lock(root, policy)
    external = _audit_external_evidence(resolver_report, pip_audit_report)
    certified = dict(_as_mapping(policy["certified_evidence"], name="certified_evidence"))
    current = dict(_as_mapping(certified["current_resolution"], name="current_resolution"))
    fallback = dict(_as_mapping(certified["cryptography_50_fallback"], name="cryptography_50_fallback"))
    incompatible = dict(_as_mapping(certified["modern_mlflow_crypto50"], name="modern_mlflow_crypto50"))

    if lock["status"] != "ok":
        reason = "qlib_lock_contract_mismatch"
    elif external["provided"] and external["security_clean"]:
        reason = "security_clean_evidence_requires_separate_policy_approval"
    else:
        reason = "upstream_constraint_blocked"

    safety = dict(_as_mapping(policy["safety"], name="safety"))
    return {
        "schema_version": "qlib_dependency_security_audit_v1",
        "status": "blocked",
        "reason": reason,
        "decision": policy["decision"],
        "policy_path": selected_policy.relative_to(root).as_posix()
        if selected_policy.is_relative_to(root)
        else selected_policy.as_posix(),
        "policy_sha256": policy_hash,
        "policy_error": None,
        "policy_status": policy["policy_status"],
        "pyqlib_version": "0.9.7",
        "approved_security_clean_resolution_found": False,
        "qlib_security_gate_passed": False,
        "lock": lock,
        "current_resolution": current,
        "cryptography_50_fallback": fallback,
        "modern_mlflow_crypto50": incompatible,
        "external_evidence": external,
        "semantic_invariants": dict(policy["semantic_invariants"]),
        **safety,
    }


def _resolve_report_destination(project_root: Path, destination: Path) -> Path:
    root = project_root.resolve()
    reports_root = (root / "data" / "reports").resolve()
    path = destination if destination.is_absolute() else root / destination
    resolved_parent = path.parent.resolve()
    resolved = resolved_parent / path.name
    if not resolved.is_relative_to(reports_root):
        raise ValueError("report_destination_outside_data_reports")
    return resolved


def write_report_atomic(project_root: Path, destination: Path, report: Mapping[str, Any]) -> Path:
    target = _resolve_report_destination(project_root, destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
        text=True,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return target
