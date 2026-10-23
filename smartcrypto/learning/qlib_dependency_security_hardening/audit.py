from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

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
REQUIRED_ADVISORIES = {
    'CVE-2026-69247': ('cryptography', '50.0.0'),
    'GHSA-22hg-r758-rrw9': ('pillow', '13.0.0'),
    'GHSA-24rc-8hj2-v98c': ('pillow', '13.0.0'),
    'GHSA-254f-3936-288h': ('pillow', '13.0.0'),
    'GHSA-26p8-2jq9-3vq9': ('mlflow', '3.17.0'),
    'GHSA-27h8-gf7j-v5hc': ('pillow', '13.0.0'),
    'GHSA-2g87-pp6c-29x5': ('aiohttp', '3.14.4'),
    'GHSA-2wqp-q3c6-ccj5': ('mlflow', '3.17.0'),
    'GHSA-332p-7hrc-92wf': ('pillow', '13.0.0'),
    'GHSA-3hv7-mjh2-fv65': ('tornado', '6.5.9'),
    'GHSA-3j72-p6qg-6xp7': ('pillow', '13.0.0'),
    'GHSA-462x-pgg6-55fp': ('pillow', '13.0.0'),
    'GHSA-494j-54xm-rwp3': ('aiohttp', '3.14.4'),
    'GHSA-4rm8-3hmm-39fg': ('pillow', '13.0.0'),
    'GHSA-4rr5-89j8-mwwm': ('pillow', '13.0.0'),
    'GHSA-54p9-h82j-f925': ('multidict', '6.9.1'),
    'GHSA-5639-2j2p-m4mx': ('mako', '1.4.2'),
    'GHSA-58r4-6rv3-gfh8': ('pillow', '13.0.0'),
    'GHSA-5wr8-gcqf-gqf3': ('aiohttp', '3.14.4'),
    'GHSA-5xm2-6676-qcp9': ('pillow', '13.0.0'),
    'GHSA-65qm-jvmh-6pqg': ('pillow', '13.0.0'),
    'GHSA-6cjc-53gc-5rqm': ('aiohttp', '3.14.4'),
    'GHSA-6q48-6c7w-9gw2': ('certifi', '2026.10.19'),
    'GHSA-74r2-7ff9-mjj2': ('pillow', '13.0.0'),
    'GHSA-78g4-xqwv-q3g8': ('pillow', '13.0.0'),
    'GHSA-7mwm-ghpf-9vrm': ('pillow', '13.0.0'),
    'GHSA-83vg-56qc-22m7': ('gitpython', '3.2.1'),
    'GHSA-8fvv-fgr5-f8ch': ('pymongo', '4.18.1'),
    'GHSA-9v9v-9qvf-h2hj': ('pillow', '13.0.0'),
    'GHSA-9xw5-w5jj-pvcg': ('aiohttp', '3.14.4'),
    'GHSA-c2m8-h5v5-343r': ('tornado', '6.5.9'),
    'GHSA-c6hw-5grf-ff96': ('pillow', '13.0.0'),
    'GHSA-chx6-46f5-w4vp': ('tornado', '6.5.9'),
    'GHSA-cp9v-662q-vvx9': ('pillow', '13.0.0'),
    'GHSA-f2g6-542v-9j58': ('pillow', '13.0.0'),
    'GHSA-f9xw-jh8m-3mwq': ('aiohttp', '3.14.4'),
    'GHSA-fc9g-5qg7-mf45': ('pillow', '13.0.0'),
    'GHSA-g4qv-2jxx-769h': ('pillow', '13.0.0'),
    'GHSA-g6x2-hccm-hh4m': ('werkzeug', '3.1.9'),
    'GHSA-g7cr-3p24-7wf7': ('pillow', '13.0.0'),
    'GHSA-g842-9rc4-69pw': ('pillow', '13.0.0'),
    'GHSA-gg2r-wvfm-q9rx': ('pillow', '13.0.0'),
    'GHSA-gqvg-gmmx-x4hm': ('mlflow', '3.15.0'),
    'GHSA-gx76-c99x-2c96': ('aiohttp', '3.14.4'),
    'GHSA-j4m9-6w36-pw47': ('pillow', '13.0.0'),
    'GHSA-j7qq-vr8c-xvw3': ('mlflow', '3.17.0'),
    'GHSA-jg86-v3jv-hrpm': ('pillow', '13.0.0'),
    'GHSA-m65p-9j7q-6w33': ('mlflow', '3.17.0'),
    'GHSA-mrfp-p49p-3hf2': ('pillow', '13.0.0'),
    'GHSA-p4wg-p73j-c528': ('pillow', '13.0.0'),
    'GHSA-p8hx-v2p6-rp84': ('pillow', '13.0.0'),
    'GHSA-pchr-8j38-3x4p': ('pillow', '13.0.0'),
    'GHSA-pqxq-w44r-73xw': ('aiohttp', '3.14.4'),
    'GHSA-q45j-58j2-45rg': ('pillow', '13.0.0'),
    'GHSA-qx36-8mw2-4r3x': ('pymongo', '4.18.2'),
    'GHSA-v4x9-3549-crwv': ('pymongo', '4.18.2'),
    'GHSA-v9pv-5gc8-w2m6': ('pillow', '13.0.0'),
    'GHSA-vp6j-j7w5-5xjj': ('pymongo', '4.18.2'),
    'GHSA-vx6w-vv9f-6gvg': ('pillow', '13.0.0'),
    'GHSA-w889-685w-c8fh': ('pillow', '13.0.0'),
    'GHSA-xcq7-p3pf-p88h': ('opentelemetry-api', '1.45.1'),
    'GHSA-xgh8-jp6q-p5x8': ('pillow', '13.0.0'),
    'PYSEC-2026-4055': ('jupyterlab', '4.6.4'),
    'PYSEC-2026-4056': ('jupyterlab', '4.6.4'),
    'PYSEC-2026-4057': ('jupyterlab', '4.6.4'),
    'PYSEC-2026-4112': ('notebook', '7.6.3'),
    'PYSEC-2026-4175': ('urllib3', '2.8.0'),
    'PYSEC-2026-4176': ('urllib3', '2.8.0'),
    'PYSEC-2026-4177': ('urllib3', '2.8.0'),
}
HISTORICAL_POLICY_EVIDENCE_SHA256 = "933ccf7aed5c17a62cc617b5e659bbab5def548a7454c9622a6ed6698c31b11b"
HISTORICAL_CERTIFICATION_SHA256 = "32aba6f1ba5406fd9d0bd167f7ddf1c2fb76480b2c55ff14ac06fdd4794d8fa1"



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


def _object_sha256(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return _sha256_bytes(canonical.encode("utf-8"))


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
    package_hashes: dict[str, list[str]] = {}
    for row in rows:
        spec = _requirement_spec(row)
        if not PIN_RE.fullmatch(spec):
            raise PolicyError(f"non_exact_pin:{path}:{spec}")
        if not HASH_RE.search(row):
            hash_locked = False
        specs.append(spec)
        name, version = spec.split("==", 1)
        name = re.sub(r"[-_.]+", "-", name.split("[", 1)[0]).lower()
        if name in packages:
            raise PolicyError(f"duplicate_package:{name}")
        packages[name] = version
        package_hashes[name] = sorted(set(value.lower() for value in HASH_RE.findall(row)))
    return {
        "sha256": _sha256_bytes(canonical),
        "requirement_count": len(rows),
        "hash_locked": hash_locked,
        "specs": specs,
        "packages": packages,
        "package_hashes": package_hashes,
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
    state = policy.get("policy_status")
    if not isinstance(state, str) or state not in {"BLOCKED", "PENDING_CERTIFICATION", "CERTIFIED"}:
        raise PolicyError("unexpected_policy_state")
    certified = state == "CERTIFIED"
    if policy.get("ci_revalidation_required") is not (not certified):
        raise PolicyError("ci_revalidation_state_mismatch")
    if (
        _object_sha256(policy.get("historical_certified_evidence"))
        != HISTORICAL_POLICY_EVIDENCE_SHA256
        or policy.get("historical_certified_evidence_sha256")
        != HISTORICAL_POLICY_EVIDENCE_SHA256
    ):
        raise PolicyError("historical_certified_evidence_changed")
    for key in ("approved_security_clean_resolution_found", "qlib_security_gate_passed", "p08_allowed"):
        if policy.get(key) is not certified:
            raise PolicyError(f"policy_state_flag_mismatch:{key}")
    if policy.get("p08_security_gate_remains_blocked") is not (not certified):
        raise PolicyError("policy_state_flag_mismatch:p08_security_gate_remains_blocked")
    for key in ("qlib_security_gate_bypassed", "p08_security_gate_bypassed"):
        if policy.get(key) is not False:
            raise PolicyError(f"unsafe_policy_flag:{key}")

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
    packages = _as_mapping(policy.get("expected_packages"), name="expected_packages")
    count = policy.get("expected_security_lock_package_count")
    if type(count) is not int or count <= 0 or count != len(packages):
        raise PolicyError("unexpected_security_lock_package_count")
    if packages.get("pyqlib") != "0.9.7" or "streamlit" in packages:
        raise PolicyError("unexpected_qlib_graph_contract")
    for name, version in packages.items():
        if not isinstance(name, str) or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) is None:
            raise PolicyError("invalid_package_name")
        if not isinstance(version, str) or not PIN_RE.fullmatch(f"{name}=={version}"):
            raise PolicyError(f"invalid_package_version:{name}")
    advisories = _as_mapping(policy.get("security_advisories"), name="security_advisories")
    for advisory_id, (name, minimum) in REQUIRED_ADVISORIES.items():
        advisory = _as_mapping(advisories.get(advisory_id), name=f"advisory:{advisory_id}")
        if advisory.get("package") != name or advisory.get("fixed_minimum") != minimum:
            raise PolicyError(f"advisory_contract_mismatch:{advisory_id}")
    for advisory_id, value in advisories.items():
        advisory = _as_mapping(value, name=f"advisory:{advisory_id}")
        advisory_package = advisory.get("package")
        source = advisory.get("source_url")
        if (
            not isinstance(advisory_package, str)
            or advisory_package not in packages
            or advisory.get("resolved_version") != packages[advisory_package]
            or not isinstance(source, str)
            or not source.startswith("https://")
            or not source.endswith(advisory_id)
        ):
            raise PolicyError(f"advisory_package_or_source_missing:{advisory_id}")
        try:
            affected = "affected_spec" in advisory and Version(packages[advisory_package]) in SpecifierSet(advisory["affected_spec"])
            below_fix = Version(packages[advisory_package]) < Version(advisory["fixed_minimum"])
        except (KeyError, TypeError, InvalidVersion, InvalidSpecifier) as exc:
            raise PolicyError(f"invalid_advisory_range:{advisory_id}") from exc
        if state != "BLOCKED" and (affected or below_fix):
            raise PolicyError(f"graph_contains_known_advisory:{advisory_id}")

    invariants = _as_mapping(policy.get("semantic_invariants"), name="semantic_invariants")
    for key in REQUIRED_SEMANTIC_INVARIANTS:
        if invariants.get(key) is not True:
            raise PolicyError(f"semantic_invariant_not_enforced:{key}")

    safety = _as_mapping(policy.get("safety"), name="safety")
    if set(safety) != set(SAFETY_DEFAULTS):
        raise PolicyError("unexpected_safety_fields")
    for key, expected in SAFETY_DEFAULTS.items():
        if safety.get(key) is not expected:
            raise PolicyError(f"unsafe_policy_flag:{key}")

    functional = _as_mapping(policy.get("p08_functional_regression"), name="p08_functional_regression")
    functional_values: dict[str, Any] = {
        "required": True, "exact_certified_graph_required": True,
        "qlib_contrib_model_required": True, "real_fit_predict_required": True,
        "fallback_allowed": False, "network_mode": "none", "repository_mount": "readonly",
        "schema_version": "qlib_security_functional_regression_v1",
    }
    for key, functional_expected in functional_values.items():
        if type(functional.get(key)) is not type(functional_expected) or functional.get(key) != functional_expected:
            raise PolicyError(f"functional_contract_mismatch:{key}")
    expected_status = {"BLOCKED": "blocked", "PENDING_CERTIFICATION": "pending_ci_certification", "CERTIFIED": "ci_certified"}[state]
    if functional.get("status") != expected_status:
        raise PolicyError("functional_status_inconsistent_with_policy_state")
    if not certified and any(
        key in functional for key in ("certified_workflow_run_id", "certified_proof_commit_sha")
    ):
        raise PolicyError("candidate_contains_current_ci_certification")
    if certified:
        approval = _as_mapping(policy.get("governance_approval"), name="governance_approval")
        if approval.get("approved") is not True or approval.get("lock_sha256") != policy["expected_security_lock_sha256"]:
            raise PolicyError("current_graph_governance_approval_missing")
        if not isinstance(approval.get("review_reference"), str) or not approval["review_reference"].strip():
            raise PolicyError("governance_review_reference_missing")
        if type(functional.get("certified_workflow_run_id")) is not int or functional["certified_workflow_run_id"] <= 0:
            raise PolicyError("certified_workflow_run_missing")
        if not isinstance(functional.get("certified_proof_commit_sha"), str) or re.fullmatch(r"[0-9a-f]{40}", functional["certified_proof_commit_sha"]) is None:
            raise PolicyError("certified_proof_commit_missing")


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
    anchors_ok = full_packages == policy["expected_packages"]
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
            raise PolicyError("invalid_resolver_package")
        metadata = item.get("metadata")
        if not isinstance(metadata, Mapping):
            raise PolicyError("invalid_resolver_metadata")
        raw_name = metadata.get("name")
        raw_version = metadata.get("version")
        if not isinstance(raw_name, str) or not isinstance(raw_version, str):
            raise PolicyError("invalid_resolver_package_identity")
        name = re.sub(r"[-_.]+", "-", raw_name.strip()).lower()
        version = raw_version.strip()
        if not name or not version or name in packages:
            raise PolicyError("missing_or_duplicate_resolver_identity")
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


def _audit_external_evidence(
    resolver_report: Path | None,
    pip_audit_report: Path | None,
    expected_packages: Mapping[str, str],
    expected_hashes: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    if resolver_report is None and pip_audit_report is None:
        return {"provided": False, "status": "not_provided", "security_clean": None, "finding_count": None, "findings": [], "resolver_packages": {}}
    if resolver_report is None or pip_audit_report is None:
        return {"provided": True, "status": "blocked", "reason": "incomplete_external_evidence_pair", "security_clean": False, "finding_count": None, "findings": [], "resolver_packages": {}}
    try:
        resolver_payload = _as_mapping(_read_json(resolver_report), name="resolver_report")
        packages = _packages_from_pip_report(resolver_payload)
        if packages != expected_packages:
            raise PolicyError("external_evidence_graph_mismatch")
        for row in _as_sequence(resolver_payload.get("install"), name="resolver_install"):
            name = re.sub(r"[-_.]+", "-", row["metadata"]["name"]).lower()
            download = _as_mapping(row.get("download_info"), name="resolver_download_info")
            archive = _as_mapping(download.get("archive_info"), name="resolver_archive_info")
            hashes = _as_mapping(archive.get("hashes"), name="resolver_archive_hashes")
            if hashes.get("sha256") not in expected_hashes.get(name, ()):
                raise PolicyError(f"resolver_archive_hash_not_in_lock:{name}")
        audit_payload = _as_mapping(_read_json(pip_audit_report), name="pip_audit_report")
        if audit_payload.get("skipped") or audit_payload.get("skips"):
            raise PolicyError("skipped_audit_dependencies")
        dependencies = _as_sequence(audit_payload.get("dependencies"), name="pip_audit_dependencies")
        audited: dict[str, str] = {}
        for dep in dependencies:
            dep = _as_mapping(dep, name="audited_dependency")
            name = re.sub(r"[-_.]+", "-", str(dep.get("name", ""))).lower()
            if "skip_reason" in dep:
                raise PolicyError(f"skipped_audit_dependency:{name}")
            if not name or not dep.get("version") or name in audited:
                raise PolicyError("incomplete_or_duplicate_audit_identity")
            _as_sequence(dep.get("vulns"), name="dependency_vulns")
            for value in dep["vulns"]:
                vuln = _as_mapping(value, name="audit_vulnerability")
                if not isinstance(vuln.get("id"), str) or not vuln["id"].strip():
                    raise PolicyError("invalid_audit_vulnerability_id")
                _as_sequence(vuln.get("fix_versions"), name="fix_versions")
            audited[name] = str(dep["version"])
        findings = _normalize_pip_audit_findings(audit_payload)
    except PolicyError as exc:
        return {"provided": True, "status": "blocked", "reason": str(exc), "security_clean": False, "finding_count": None, "findings": [], "resolver_packages": {}}
    anchors_ok = packages == expected_packages and audited == expected_packages
    clean = bool(packages) and not findings and anchors_ok
    return {
        "provided": True,
        "status": "ok" if clean else "blocked",
        "reason": "external_evidence_security_clean" if clean else ("external_evidence_graph_mismatch" if not findings else "external_evidence_contains_findings"),
        "security_clean": clean,
        "finding_count": len(findings),
        "findings": findings,
        "resolver_packages": packages,
        "audited_packages": audited,
        "expected_package_count": len(expected_packages),
        "audited_package_count": len(audited),
        "missing_packages": sorted(set(expected_packages) - set(audited)),
        "unexpected_packages": sorted(set(audited) - set(expected_packages)),
        "version_mismatches": sorted(
            name for name in set(audited) & set(expected_packages)
            if audited[name] != expected_packages[name]
        ),
        "anchor_packages_match": anchors_ok,
    }


def _current_evidence_valid(evidence: Mapping[str, Any], policy: Mapping[str, Any]) -> bool:
    """Historical certificates cannot satisfy the current graph's requirements."""
    expected = policy["expected_packages"]
    valid = (
        evidence.get("hashed_lock_sha256") == policy["expected_security_lock_sha256"]
        and evidence.get("packages") == expected
        and evidence.get("resolved_package_count") == len(expected)
        and evidence.get("audited_dependency_count") == len(expected)
        and evidence.get("resolver_status") == "resolved"
        and evidence.get("security_status") == "clean"
        and evidence.get("pip_audit_exit_code") == 0
        and evidence.get("known_vulnerability_count") == 0
        and evidence.get("unreviewed_advisory_count") == 0
        and evidence.get("hashed_install_status") == "PASS"
        and evidence.get("python_version") == "3.12"
        and evidence.get("platform") == "linux"
        and evidence.get("skipped_dependency_count") == 0
        and type(evidence.get("skipped_dependency_count")) is int
        and evidence.get("ci_revalidation_required") is (policy["policy_status"] != "CERTIFIED")
        and evidence.get("advisories_reviewed") == sorted(policy["security_advisories"])
        and all(type(evidence.get(key)) is int for key in (
            "resolved_package_count", "audited_dependency_count", "pip_audit_exit_code",
            "known_vulnerability_count", "unreviewed_advisory_count",
        ))
        and all(_valid_sha(evidence.get(key)) for key in (
            "resolver_report_sha256", "pip_audit_report_sha256", "hashed_install_report_sha256",
        ))
    )
    if policy["policy_status"] == "CERTIFIED":
        functional = evidence.get("functional_regression", {})
        valid = valid and isinstance(functional, Mapping) and all((
            functional.get("status") == "PASS",
            functional.get("lock_sha256") == policy["expected_security_lock_sha256"],
            functional.get("fallback_used") is False,
            functional.get("deterministic_repeat") is True,
            functional.get("network_mode") == "none",
            functional.get("repository_mount") == "readonly",
            _valid_sha(functional.get("report_sha256")),
            functional.get("workflow_run_id") == policy["p08_functional_regression"]["certified_workflow_run_id"],
            functional.get("proof_commit_sha") == policy["p08_functional_regression"]["certified_proof_commit_sha"],
        ))
    return bool(valid)


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
            "policy_valid": False,
            "p08_allowed": False,
            "qlib_security_gate_remains_blocked": True,
            "approved_security_clean_resolution_found": False,
            "qlib_security_gate_passed": False,
            "lock": {"status": "blocked", "reason": "policy_unavailable"},
            "external_evidence": {"provided": False, "status": "not_evaluated", "security_clean": False},
            **SAFETY_DEFAULTS,
        }

    lock = _audit_locks(root, policy)
    evidence_path = root / str(policy["security_resolution_evidence_file"])
    evidence_identity = _audit_file_identity(evidence_path, str(policy["expected_security_resolution_evidence_sha256"]))
    external = _audit_external_evidence(
        resolver_report,
        pip_audit_report,
        policy["expected_packages"],
        lock["security_lock"].get("package_hashes", {}),
    )

    try:
        versioned_evidence = dict(_as_mapping(_read_json(evidence_path), name="versioned_security_resolution_evidence"))
    except PolicyError:
        versioned_evidence = {}
    qlib_evidence = versioned_evidence.get("qlib", {}) if isinstance(versioned_evidence, Mapping) else {}
    versioned_clean = isinstance(qlib_evidence, Mapping) and _current_evidence_valid(qlib_evidence, policy)
    historical = versioned_evidence.get("historical_certifications", {})
    historical_valid = (
        isinstance(historical, Mapping)
        and _object_sha256(historical.get("2026-10-06")) == HISTORICAL_CERTIFICATION_SHA256
        and versioned_evidence.get("historical_certification_object_sha256")
        == HISTORICAL_CERTIFICATION_SHA256
    )
    if policy["policy_status"] == "PENDING_CERTIFICATION":
        versioned_clean = bool(
            versioned_clean
            and versioned_evidence.get("evidence_status") == "pending_certification"
            and versioned_evidence.get("ci_certification_status") == "pending_ci_certification"
            and versioned_evidence.get("ci_revalidation_required") is True
        )

    blockers: list[str] = []
    if lock["status"] != "ok":
        blockers.append("qlib_lock_contract_mismatch")
    if evidence_identity["status"] != "ok":
        blockers.append("security_resolution_evidence_identity_mismatch")
    if not historical_valid:
        blockers.append("historical_certification_changed")
    if not versioned_clean and policy["policy_status"] != "BLOCKED":
        blockers.append("versioned_security_resolution_not_clean")
    if external["provided"] and external["status"] != "ok":
        blockers.append(str(external.get("reason", "external_evidence_blocked")))

    candidate_evidence_valid = not blockers and versioned_clean
    if policy["policy_status"] == "BLOCKED":
        blockers.append("qlib_security_blocked")
    passed = not blockers and policy["policy_status"] == "CERTIFIED"
    pending = candidate_evidence_valid and policy["policy_status"] == "PENDING_CERTIFICATION"
    safety = {key: policy["safety"][key] for key in SAFETY_DEFAULTS}
    clean = dict(qlib_evidence) if isinstance(qlib_evidence, Mapping) else {}
    return {
        "schema_version": "qlib_dependency_security_audit_v1",
        "status": "ok" if passed else ("pending_certification" if pending else "blocked"),
        "reason": (
            "approved_security_clean_resolution_certified" if passed
            else ("security_clean_candidate_pending_ci" if pending else blockers[0])
        ),
        "blockers": blockers,
        "certification_blockers": [] if passed else ["independent_current_graph_ci_certification_required"],
        "decision": policy["decision"],
        "policy_path": selected_policy.relative_to(root).as_posix() if selected_policy.is_relative_to(root) else selected_policy.as_posix(),
        "policy_sha256": policy_hash,
        "policy_error": None,
        "policy_valid": True,
        "policy_status": policy["policy_status"],
        "candidate_evidence_valid": candidate_evidence_valid,
        "p08_allowed": passed and policy["p08_allowed"] is True,
        "qlib_security_gate_remains_blocked": not passed,
        "pyqlib_version": "0.9.7",
        "approved_security_clean_resolution_found": passed,
        "qlib_security_gate_passed": passed,
        "lock": lock,
        "security_resolution_evidence_identity": evidence_identity,
        "security_clean_resolution": clean,
        "historical_evidence": policy.get("historical_certified_evidence"),
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
