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
HASH_RE = re.compile(r"--hash=sha256:([0-9a-fA-F]{64})")
PIN_RE = re.compile(r"^[A-Za-z0-9_.-]+(?:\[[^]]+\])?==[^;\s]+$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
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
ANCHOR_PACKAGES = {
    "pyqlib": "0.9.7",
    "mlflow": "3.16.0",
    "cryptography": "50.0.0",
    "pyarrow": "25.0.1",
}


class PolicyError(ValueError):
    """Raised when the versioned dependency-security policy is invalid."""


def _canonical_text_bytes(data: bytes) -> bytes:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise PolicyError("text_input_must_be_utf8") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def _valid_sha(value: Any) -> bool:
    return isinstance(value, str) and SHA_RE.fullmatch(value) is not None


def _logical_lines(text: str) -> list[str]:
    rows: list[str] = []
    buffer: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not buffer and (not stripped or stripped.startswith("#")):
            continue
        buffer.append(stripped.rstrip("\\").strip())
        if stripped.endswith("\\"):
            continue
        combined = " ".join(part for part in buffer if part).split(" #", 1)[0].strip()
        if combined:
            rows.append(combined)
        buffer = []
    if buffer:
        rows.append(" ".join(buffer).strip())
    return rows


def _requirement_spec(line: str) -> str:
    marker = line.find(" --hash=")
    return (line[:marker] if marker >= 0 else line).strip()


def _parse_hashed_lock(path: Path) -> dict[str, Any]:
    raw = _read_bytes(path)
    canonical = _canonical_text_bytes(raw)
    text = canonical.decode("utf-8")
    rows = _logical_lines(text)
    specs: list[str] = []
    hash_locked = True
    packages: dict[str, str] = {}
    for row in rows:
        spec = _requirement_spec(row)
        if not PIN_RE.fullmatch(spec):
            raise PolicyError(f"non_exact_pin:{path}:{spec}")
        if not HASH_RE.search(row):
            hash_locked = False
        specs.append(spec)
        name, version = spec.split("==", 1)
        packages[name.lower().replace("_", "-")] = version
    return {
        "sha256": _sha256_bytes(canonical),
        "requirement_count": len(rows),
        "hash_locked": hash_locked,
        "specs": specs,
        "packages": packages,
    }


def load_policy(path: Path) -> dict[str, Any]:
    payload = dict(_as_mapping(_read_json(path), name="policy"))
    _validate_policy(payload)
    return payload


def _validate_policy(policy: Mapping[str, Any]) -> None:
    if policy.get("schema_version") != EXPECTED_SCHEMA_VERSION:
        raise PolicyError("unexpected_schema_version")
    if policy.get("decision") != EXPECTED_DECISION:
        raise PolicyError("unexpected_decision")
    if policy.get("policy_status") != "active_security_clean":
        raise PolicyError("policy_not_security_clean")
    if policy.get("approved_security_clean_resolution_found") is not True:
        raise PolicyError("security_clean_resolution_not_approved")
    if policy.get("qlib_security_gate_passed") is not True:
        raise PolicyError("qlib_security_gate_not_passed")

    for key in (
        "expected_requirements_sha256",
        "expected_security_lock_sha256",
        "expected_security_resolution_evidence_sha256",
    ):
        if not _valid_sha(policy.get(key)):
            raise PolicyError(f"invalid_{key}")

    if policy.get("requirements_file") != "requirements-qlib.lock":
        raise PolicyError("unexpected_requirements_file")
    if policy.get("security_lock_file") != "requirements-qlib-security.lock":
        raise PolicyError("unexpected_security_lock_file")
    if policy.get("security_resolution_evidence_file") != "config/security_resolution_evidence_v1.json":
        raise PolicyError("unexpected_security_resolution_evidence_file")
    if list(_as_sequence(policy.get("expected_direct_requirements"), name="expected_direct_requirements")) != ["pyqlib==0.9.7"]:
        raise PolicyError("unexpected_direct_requirements_contract")
    if int(policy.get("expected_security_lock_package_count", 0)) != 190:
        raise PolicyError("unexpected_security_lock_package_count")

    invariants = _as_mapping(policy.get("semantic_invariants"), name="semantic_invariants")
    for key in REQUIRED_SEMANTIC_INVARIANTS:
        if invariants.get(key) is not True:
            raise PolicyError(f"semantic_invariant_not_enforced:{key}")

    safety = _as_mapping(policy.get("safety"), name="safety")
    for key, expected in SAFETY_DEFAULTS.items():
        if safety.get(key) is not expected:
            raise PolicyError(f"unsafe_policy_flag:{key}")

    certified = _as_mapping(policy.get("certified_evidence"), name="certified_evidence")
    clean = _as_mapping(certified.get("security_clean_resolution"), name="security_clean_resolution")
    if clean.get("resolver_status") != "resolved":
        raise PolicyError("certified_resolver_not_resolved")
    if clean.get("security_status") != "clean":
        raise PolicyError("certified_security_status_not_clean")
    if int(clean.get("pip_audit_exit_code", -1)) != 0:
        raise PolicyError("certified_pip_audit_failed")
    if int(clean.get("known_vulnerability_count", -1)) != 0:
        raise PolicyError("certified_vulnerability_count_nonzero")
    if int(clean.get("resolved_package_count", 0)) != 190:
        raise PolicyError("certified_package_count_mismatch")
    if str(clean.get("hashed_lock_sha256", "")) != str(policy["expected_security_lock_sha256"]):
        raise PolicyError("certified_lock_sha_mismatch")
    packages = _as_mapping(clean.get("packages"), name="certified_packages")
    for name, version in ANCHOR_PACKAGES.items():
        if str(packages.get(name, "")) != version:
            raise PolicyError(f"certified_anchor_package_mismatch:{name}")


def _audit_file_identity(path: Path, expected_sha: str) -> dict[str, Any]:
    if not path.is_file():
        return {"status": "blocked", "reason": "file_missing", "sha256": None, "expected_sha256": expected_sha}
    try:
        digest = _sha256_bytes(_canonical_text_bytes(_read_bytes(path)))
    except PolicyError as exc:
        return {"status": "blocked", "reason": str(exc), "sha256": None, "expected_sha256": expected_sha}
    return {
        "status": "ok" if digest == expected_sha else "blocked",
        "reason": "identity_match" if digest == expected_sha else "identity_mismatch",
        "sha256": digest,
        "expected_sha256": expected_sha,
    }


def _audit_locks(root: Path, policy: Mapping[str, Any]) -> dict[str, Any]:
    direct_path = root / str(policy["requirements_file"])
    full_path = root / str(policy["security_lock_file"])
    try:
        direct = _parse_hashed_lock(direct_path)
    except PolicyError as exc:
        direct = {"error": str(exc), "sha256": None, "requirement_count": 0, "hash_locked": False, "specs": [], "packages": {}}
    try:
        full = _parse_hashed_lock(full_path)
    except PolicyError as exc:
        full = {"error": str(exc), "sha256": None, "requirement_count": 0, "hash_locked": False, "specs": [], "packages": {}}

    expected_direct = list(policy["expected_direct_requirements"])
    direct_ok = (
        direct.get("sha256") == policy["expected_requirements_sha256"]
        and direct.get("specs") == expected_direct
        and direct.get("hash_locked") is True
    )
    full_packages = full.get("packages", {})
    anchors_ok = all(full_packages.get(name) == version for name, version in ANCHOR_PACKAGES.items())
    full_ok = (
        full.get("sha256") == policy["expected_security_lock_sha256"]
        and full.get("requirement_count") == int(policy["expected_security_lock_package_count"])
        and full.get("hash_locked") is True
        and anchors_ok
    )
    return {
        "status": "ok" if direct_ok and full_ok else "blocked",
        "reason": "qlib_lock_contract_ok" if direct_ok and full_ok else "qlib_lock_contract_mismatch",
        "direct": direct,
        "security_lock": full,
        "direct_requirements_match_policy": direct.get("specs") == expected_direct,
        "security_lock_anchors_match_policy": anchors_ok,
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
    if isinstance(payload, Mapping):
        deps = payload.get("dependencies", payload.get("vulnerabilities", []))
    else:
        deps = payload
    dependencies = _as_sequence(deps, name="pip_audit_dependencies")
    findings: list[dict[str, Any]] = []
    for dep in dependencies:
        if not isinstance(dep, Mapping):
            continue
        name = str(dep.get("name", dep.get("package", ""))).strip()
        version = str(dep.get("version", "")).strip()
        vulns = dep.get("vulns", dep.get("vulnerabilities", []))
        if isinstance(vulns, Sequence) and not isinstance(vulns, (str, bytes, bytearray)):
            for vuln in vulns:
                if isinstance(vuln, Mapping):
                    findings.append({
                        "package": name,
                        "version": version,
                        "id": str(vuln.get("id", vuln.get("name", "UNKNOWN"))),
                        "fix_versions": list(vuln.get("fix_versions", [])) if isinstance(vuln.get("fix_versions", []), Sequence) and not isinstance(vuln.get("fix_versions", []), (str, bytes, bytearray)) else [],
                    })
    return sorted(findings, key=lambda row: (row["package"], row["version"], row["id"]))


def _audit_external_evidence(resolver_report: Path | None, pip_audit_report: Path | None) -> dict[str, Any]:
    if resolver_report is None and pip_audit_report is None:
        return {"provided": False, "status": "not_provided", "security_clean": None, "finding_count": None, "findings": [], "resolver_packages": {}}
    if resolver_report is None or pip_audit_report is None:
        return {"provided": True, "status": "blocked", "reason": "incomplete_external_evidence_pair", "security_clean": False, "finding_count": None, "findings": [], "resolver_packages": {}}
    try:
        packages = _packages_from_pip_report(_read_json(resolver_report))
        findings = _normalize_pip_audit_findings(_read_json(pip_audit_report))
    except PolicyError as exc:
        return {"provided": True, "status": "blocked", "reason": str(exc), "security_clean": False, "finding_count": None, "findings": [], "resolver_packages": {}}
    anchors_ok = all(packages.get(name) == version for name, version in ANCHOR_PACKAGES.items())
    clean = bool(packages) and not findings and anchors_ok
    return {
        "provided": True,
        "status": "ok" if clean else "blocked",
        "reason": "external_evidence_security_clean" if clean else ("external_evidence_anchor_mismatch" if not findings else "external_evidence_contains_findings"),
        "security_clean": clean,
        "finding_count": len(findings),
        "findings": findings,
        "resolver_packages": packages,
        "anchor_packages_match": anchors_ok,
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
    try:
        canonical = _canonical_text_bytes(_read_bytes(selected_policy))
        policy_hash = _sha256_bytes(canonical)
        policy = dict(_as_mapping(json.loads(canonical.decode("utf-8")), name="policy"))
        _validate_policy(policy)
    except (PolicyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {
            "schema_version": "qlib_dependency_security_audit_v1",
            "status": "blocked",
            "reason": "policy_invalid_or_incomplete",
            "decision": EXPECTED_DECISION,
            "policy_path": selected_policy.as_posix(),
            "policy_sha256": policy_hash,
            "policy_error": str(exc),
            "approved_security_clean_resolution_found": False,
            "qlib_security_gate_passed": False,
            "lock": {"status": "blocked", "reason": "policy_unavailable"},
            "external_evidence": {"provided": False, "status": "not_evaluated", "security_clean": False},
            **SAFETY_DEFAULTS,
        }

    lock = _audit_locks(root, policy)
    evidence_path = root / str(policy["security_resolution_evidence_file"])
    evidence_identity = _audit_file_identity(evidence_path, str(policy["expected_security_resolution_evidence_sha256"]))
    external = _audit_external_evidence(resolver_report, pip_audit_report)

    try:
        versioned_evidence = dict(_as_mapping(_read_json(evidence_path), name="versioned_security_resolution_evidence"))
    except PolicyError:
        versioned_evidence = {}
    qlib_evidence = versioned_evidence.get("qlib", {}) if isinstance(versioned_evidence, Mapping) else {}
    versioned_clean = (
        isinstance(qlib_evidence, Mapping)
        and qlib_evidence.get("resolver_status") == "resolved"
        and qlib_evidence.get("security_status") == "clean"
        and int(qlib_evidence.get("pip_audit_exit_code", -1)) == 0
        and int(qlib_evidence.get("known_vulnerability_count", -1)) == 0
        and int(qlib_evidence.get("resolved_package_count", 0)) == 190
        and str(qlib_evidence.get("hashed_lock_sha256", "")) == str(policy["expected_security_lock_sha256"])
        and all(str(qlib_evidence.get("packages", {}).get(name, "")) == version for name, version in ANCHOR_PACKAGES.items())
    )

    blockers: list[str] = []
    if lock["status"] != "ok":
        blockers.append("qlib_lock_contract_mismatch")
    if evidence_identity["status"] != "ok":
        blockers.append("security_resolution_evidence_identity_mismatch")
    if not versioned_clean:
        blockers.append("versioned_security_resolution_not_clean")
    if external["provided"] and external["status"] != "ok":
        blockers.append(str(external.get("reason", "external_evidence_blocked")))

    passed = not blockers
    safety = dict(_as_mapping(policy["safety"], name="safety"))
    clean = dict(_as_mapping(policy["certified_evidence"]["security_clean_resolution"], name="security_clean_resolution"))
    return {
        "schema_version": "qlib_dependency_security_audit_v1",
        "status": "ok" if passed else "blocked",
        "reason": "approved_security_clean_resolution_certified" if passed else blockers[0],
        "blockers": blockers,
        "decision": policy["decision"],
        "policy_path": selected_policy.relative_to(root).as_posix() if selected_policy.is_relative_to(root) else selected_policy.as_posix(),
        "policy_sha256": policy_hash,
        "policy_error": None,
        "policy_status": policy["policy_status"],
        "pyqlib_version": "0.9.7",
        "approved_security_clean_resolution_found": passed,
        "qlib_security_gate_passed": passed,
        "lock": lock,
        "security_resolution_evidence_identity": evidence_identity,
        "security_clean_resolution": clean,
        "historical_evidence": policy["certified_evidence"].get("historical_blocked_resolution"),
        "external_evidence": external,
        "semantic_invariants": dict(policy["semantic_invariants"]),
        **safety,
    }


def _resolve_report_destination(project_root: Path, destination: Path) -> Path:
    root = project_root.resolve()
    reports_root = (root / "data" / "reports").resolve()
    path = destination if destination.is_absolute() else root / destination
    resolved = path.parent.resolve() / path.name
    if not resolved.is_relative_to(reports_root):
        raise ValueError("report_destination_outside_data_reports")
    return resolved


def write_report_atomic(project_root: Path, destination: Path, report: Mapping[str, Any]) -> Path:
    target = _resolve_report_destination(project_root, destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent), text=True)
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
