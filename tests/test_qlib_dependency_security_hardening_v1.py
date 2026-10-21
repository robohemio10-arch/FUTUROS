\
from __future__ import annotations

import importlib.util
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_qlib_dependency_security_hardening_v1.py"
POLICY = ROOT / "config" / "qlib_dependency_security_policy_v1.json"
DIRECT_LOCK = ROOT / "requirements-qlib.lock"
SECURITY_LOCK = ROOT / "requirements-qlib-security.lock"
EVIDENCE = ROOT / "config" / "security_resolution_evidence_v1.json"


def load_module() -> Any:
    module_path = ROOT / "smartcrypto" / "learning" / "qlib_dependency_security_hardening" / "audit.py"
    spec = importlib.util.spec_from_file_location("qlib_dependency_security_hardening_test", module_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def copy_project(root: Path, *, certified: bool = True) -> None:
    for source, relative in (
        (POLICY, "config/qlib_dependency_security_policy_v1.json"),
        (DIRECT_LOCK, "requirements-qlib.lock"),
        (SECURITY_LOCK, "requirements-qlib-security.lock"),
        (EVIDENCE, "config/security_resolution_evidence_v1.json"),
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    if not certified:
        return
    # A current certificate exists only in this isolated positive fixture.
    policy_path = root / "config/qlib_dependency_security_policy_v1.json"
    evidence_path = root / "config/security_resolution_evidence_v1.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy.update({
        "policy_status": "CERTIFIED", "p08_allowed": True,
        "qlib_security_gate_passed": True, "approved_security_clean_resolution_found": True,
        "p08_security_gate_remains_blocked": False,
        "governance_approval": {"approved": True, "lock_sha256": policy["expected_security_lock_sha256"], "review_reference": "ISOLATED_FIXTURE_ONLY"},
    })
    policy["p08_functional_regression"].update({
        "status": "ci_certified", "certified_workflow_run_id": 123,
        "certified_proof_commit_sha": "a" * 40,
    })
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["qlib"]["functional_regression"] = {
        "status": "PASS", "lock_sha256": policy["expected_security_lock_sha256"],
        "fallback_used": False, "deterministic_repeat": True, "network_mode": "none",
        "repository_mount": "readonly", "report_sha256": "b" * 64,
        "workflow_run_id": 123, "proof_commit_sha": "a" * 40,
    }
    write_json(evidence_path, evidence)
    policy["expected_security_resolution_evidence_sha256"] = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    write_json(policy_path, policy)


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def clean_external_evidence(root: Path) -> tuple[Path, Path]:
    packages = json.loads((root / "config/qlib_dependency_security_policy_v1.json").read_text(encoding="utf-8"))["expected_packages"]
    resolver = write_json(
        root / "resolver.json",
        {
            "install": [{"metadata": {"name": n, "version": v}} for n, v in packages.items()]
        },
    )
    audit = write_json(root / "audit.json", {"dependencies": [{"name": n, "version": v, "vulns": []} for n, v in packages.items()]})
    return resolver, audit


def test_current_repository_is_pending_without_p08_authority() -> None:
    module = load_module()
    report = module.audit_project(ROOT)

    assert report["status"] == "blocked"
    assert report["reason"] == "qlib_pending_certification"
    assert report["blockers"] == ["qlib_pending_certification"]
    assert report["policy_valid"] is True
    assert report["candidate_evidence_valid"] is True
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["approved_security_clean_resolution_found"] is False
    assert report["qlib_security_gate_passed"] is False
    assert report["p08_allowed"] is False
    assert report["operational_authority"] is False
    assert report["runtime_updated"] is False
    assert report["model_promotion_performed"] is False


def test_certified_anchor_versions_and_zero_vulnerabilities() -> None:
    report = load_module().audit_project(ROOT)
    evidence = report["security_clean_resolution"]

    assert evidence["resolver_status"] == "resolved"
    assert evidence["security_status"] == "clean"
    assert evidence["pip_audit_exit_code"] == 0
    assert evidence["known_vulnerability_count"] == 0
    assert evidence["resolved_package_count"] == 189
    assert evidence["packages"]["pyqlib"] == "0.9.7"
    assert evidence["packages"]["mlflow"] == "3.17.0"
    assert evidence["packages"]["cryptography"] == "50.0.0"
    assert evidence["packages"]["pyarrow"] == "25.0.1"


def test_direct_and_full_locks_are_hash_locked() -> None:
    report = load_module().audit_project(ROOT)
    lock = report["lock"]

    assert lock["status"] == "ok"
    assert lock["direct"]["requirement_count"] == 1
    assert lock["direct"]["hash_locked"] is True
    assert lock["direct"]["specs"] == ["pyqlib==0.9.7"]
    assert lock["security_lock"]["requirement_count"] == 189
    assert lock["security_lock"]["hash_locked"] is True
    assert lock["security_lock_anchors_match_policy"] is True


def test_missing_policy_is_blocked(tmp_path: Path) -> None:
    copy_project(tmp_path)
    (tmp_path / "config/qlib_dependency_security_policy_v1.json").unlink()

    report = load_module().audit_project(tmp_path)
    assert report["status"] == "blocked"
    assert report["reason"] == "policy_invalid_or_incomplete"
    assert report["qlib_security_gate_passed"] is False


def test_direct_lock_tamper_is_blocked(tmp_path: Path) -> None:
    copy_project(tmp_path)
    path = tmp_path / "requirements-qlib.lock"
    path.write_text(path.read_text() + "# tamper\n", encoding="utf-8")

    report = load_module().audit_project(tmp_path)
    assert report["status"] == "blocked"
    assert "qlib_lock_contract_mismatch" in report["blockers"]


def test_full_security_lock_tamper_is_blocked(tmp_path: Path) -> None:
    copy_project(tmp_path)
    path = tmp_path / "requirements-qlib-security.lock"
    path.write_text(path.read_text().replace("cryptography==50.0.0", "cryptography==49.0.0", 1), encoding="utf-8")

    report = load_module().audit_project(tmp_path)
    assert report["status"] == "blocked"
    assert "qlib_lock_contract_mismatch" in report["blockers"]


def test_versioned_evidence_tamper_is_blocked(tmp_path: Path) -> None:
    copy_project(tmp_path)
    path = tmp_path / "config/security_resolution_evidence_v1.json"
    payload = json.loads(path.read_text())
    payload["qlib"]["known_vulnerability_count"] = 1
    path.write_text(json.dumps(payload), encoding="utf-8")

    report = load_module().audit_project(tmp_path)
    assert report["status"] == "blocked"
    assert "security_resolution_evidence_identity_mismatch" in report["blockers"]


def test_incomplete_external_evidence_pair_is_blocked(tmp_path: Path) -> None:
    copy_project(tmp_path)
    resolver, _ = clean_external_evidence(tmp_path)
    report = load_module().audit_project(tmp_path, resolver_report=resolver)

    assert report["status"] == "blocked"
    assert report["external_evidence"]["reason"] == "incomplete_external_evidence_pair"


def test_external_clean_matching_evidence_is_accepted(tmp_path: Path) -> None:
    copy_project(tmp_path)
    resolver, audit = clean_external_evidence(tmp_path)
    report = load_module().audit_project(tmp_path, resolver_report=resolver, pip_audit_report=audit)

    assert report["status"] == "ok"
    assert report["external_evidence"]["security_clean"] is True
    assert report["external_evidence"]["anchor_packages_match"] is True


def test_external_unknown_finding_is_blocking(tmp_path: Path) -> None:
    copy_project(tmp_path)
    resolver, audit = clean_external_evidence(tmp_path)
    write_json(
        audit,
        {
            "dependencies": [
                {
                    "name": "some-package",
                    "version": "1.0.0",
                    "vulns": [{"id": "UNKNOWN-NEW-FINDING", "fix_versions": []}],
                }
            ]
        },
    )
    report = load_module().audit_project(tmp_path, resolver_report=resolver, pip_audit_report=audit)

    assert report["status"] == "blocked"
    assert report["external_evidence"]["finding_count"] == 1
    assert report["external_evidence"]["security_clean"] is False


def test_external_anchor_mismatch_is_blocking(tmp_path: Path) -> None:
    copy_project(tmp_path)
    resolver, audit = clean_external_evidence(tmp_path)
    payload = json.loads(resolver.read_text())
    payload["install"][1]["metadata"]["version"] = "3.15.1"
    write_json(resolver, payload)

    report = load_module().audit_project(tmp_path, resolver_report=resolver, pip_audit_report=audit)
    assert report["status"] == "blocked"
    assert report["external_evidence"]["reason"] == "external_evidence_graph_mismatch"


def test_default_execution_is_deterministic() -> None:
    module = load_module()
    assert module.audit_project(ROOT) == module.audit_project(ROOT)


def test_default_execution_does_not_write() -> None:
    before = DIRECT_LOCK.read_bytes()
    report = load_module().audit_project(ROOT)
    assert report["status"] == "blocked"
    assert DIRECT_LOCK.read_bytes() == before


def test_write_report_restricted_to_data_reports(tmp_path: Path) -> None:
    copy_project(tmp_path)
    module = load_module()
    report = module.audit_project(tmp_path)
    with pytest.raises(ValueError, match="report_destination_outside_data_reports"):
        module.write_report_atomic(tmp_path, Path("outside.json"), report)


def test_write_report_atomic_contains_success(tmp_path: Path) -> None:
    copy_project(tmp_path)
    module = load_module()
    report = module.audit_project(tmp_path)
    target = module.write_report_atomic(tmp_path, Path("data/reports/qlib_dependency_security_hardening_v1.json"), report)
    payload = json.loads(target.read_text())
    assert payload["status"] == "ok"
    assert payload["qlib_security_gate_passed"] is True
    assert not list(target.parent.glob("*.tmp"))


def test_safety_flags_remain_fail_closed_for_operations() -> None:
    report = load_module().audit_project(ROOT)
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["research_only"] is True
    assert report["operational_authority"] is False
    assert report["runtime_updated"] is False
    assert report["models_changed"] is False
    assert report["model_promotion_performed"] is False
    assert report["changes_risk"] is False
    assert report["sends_orders"] is False
    assert report["exchange_private_access"] is False
    assert report["live_trading_enabled"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False
    assert report["canary_release_allowed"] is False
    assert report["live_release_allowed"] is False


def test_auditor_source_is_static_and_offline() -> None:
    combined = (
        (ROOT / "smartcrypto/learning/qlib_dependency_security_hardening/audit.py").read_text().lower()
        + "\n"
        + SCRIPT.read_text().lower()
    )
    for marker in ("subprocess.run", "subprocess.popen", "requests.", "urllib.request", "httpx.", "import docker", "import ccxt", "pip install", "shell=true", "--ignore-vuln"):
        assert marker not in combined


def test_cli_reports_pending_as_blocked_without_writing() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(ROOT), "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert payload["status"] == "blocked"
    assert payload["reason"] == "qlib_pending_certification"
    assert payload["qlib_security_gate_passed"] is False
    assert payload["write_requested"] is False
    assert payload["write_performed"] is False


def test_cli_persisted_report_truthfully_records_write(tmp_path: Path) -> None:
    copy_project(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project-root",
            str(tmp_path),
            "--write-report",
            "data/reports/qlib_dependency_security_hardening_v1.json",
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(completed.stdout)
    assert completed.returncode == 0
    assert payload["status"] == "ok"
    assert payload["write_requested"] is True
    assert payload["write_performed"] is True


def rebind_evidence(root: Path, evidence: dict[str, Any]) -> None:
    path = write_json(root / "config/security_resolution_evidence_v1.json", evidence)
    policy_path = root / "config/qlib_dependency_security_policy_v1.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["expected_security_resolution_evidence_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    write_json(policy_path, policy)


@pytest.mark.parametrize("state", ["BLOCKED", "PENDING_CERTIFICATION", "CERTIFIED"])
def test_three_valid_governance_states(tmp_path: Path, state: str) -> None:
    copy_project(tmp_path)
    path = tmp_path / "config/qlib_dependency_security_policy_v1.json"
    policy = json.loads(path.read_text(encoding="utf-8"))
    certified = state == "CERTIFIED"
    policy.update({"policy_status": state, "p08_allowed": certified,
                   "qlib_security_gate_passed": certified,
                   "approved_security_clean_resolution_found": certified,
                   "p08_security_gate_remains_blocked": not certified})
    policy["p08_functional_regression"]["status"] = {
        "BLOCKED": "blocked", "PENDING_CERTIFICATION": "pending_ci_certification",
        "CERTIFIED": "ci_certified",
    }[state]
    write_json(path, policy)
    report = load_module().audit_project(tmp_path)
    assert report["policy_valid"] is True
    assert report["policy_error"] is None
    assert report["p08_allowed"] is certified
    assert report["status"] == ("ok" if certified else "blocked")


@pytest.mark.parametrize("flag", ["p08_allowed", "qlib_security_gate_passed", "approved_security_clean_resolution_found"])
def test_pending_cannot_grant_approval(tmp_path: Path, flag: str) -> None:
    copy_project(tmp_path)
    path = tmp_path / "config/qlib_dependency_security_policy_v1.json"
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    policy[flag] = True
    write_json(path, policy)
    report = load_module().audit_project(tmp_path)
    assert report["reason"] == "policy_invalid_or_incomplete"
    assert report["p08_allowed"] is False


@pytest.mark.parametrize("advisory_id", sorted(load_module().REQUIRED_ADVISORIES))
def test_every_required_advisory_is_mandatory(tmp_path: Path, advisory_id: str) -> None:
    copy_project(tmp_path)
    path = tmp_path / "config/qlib_dependency_security_policy_v1.json"
    policy = json.loads(path.read_text(encoding="utf-8"))
    del policy["security_advisories"][advisory_id]
    write_json(path, policy)
    report = load_module().audit_project(tmp_path)
    assert report["policy_valid"] is False
    assert f"advisory:{advisory_id}" in report["policy_error"]


@pytest.mark.parametrize("key,value", [
    ("known_vulnerability_count", 1), ("unreviewed_advisory_count", 1),
    ("hashed_install_status", "FAIL"), ("resolver_report_sha256", None),
    ("audited_dependency_count", 4), ("advisories_reviewed", []),
    ("python_version", "3.11"), ("platform", "windows"),
    ("functional_regression", {"status": "PASS"}),
])
def test_current_evidence_must_be_complete(tmp_path: Path, key: str, value: Any) -> None:
    copy_project(tmp_path)
    evidence = json.loads((tmp_path / "config/security_resolution_evidence_v1.json").read_text(encoding="utf-8"))
    evidence["qlib"][key] = value
    rebind_evidence(tmp_path, evidence)
    assert load_module().audit_project(tmp_path)["reason"] == "versioned_security_resolution_not_clean"


def test_historical_certificate_cannot_satisfy_current_graph(tmp_path: Path) -> None:
    copy_project(tmp_path)
    evidence = json.loads((tmp_path / "config/security_resolution_evidence_v1.json").read_text(encoding="utf-8"))
    evidence["qlib"] = evidence["historical_certifications"]["2026-10-06"]["qlib"]
    rebind_evidence(tmp_path, evidence)
    assert load_module().audit_project(tmp_path)["p08_allowed"] is False


@pytest.mark.parametrize("mutation", ["empty", "missing", "duplicate", "skipped", "wrong_version"])
def test_external_incomplete_graph_is_blocked(tmp_path: Path, mutation: str) -> None:
    copy_project(tmp_path)
    resolver, audit = clean_external_evidence(tmp_path)
    payload = json.loads(audit.read_text(encoding="utf-8"))
    deps = payload["dependencies"]
    if mutation == "empty":
        deps.clear()
    elif mutation == "missing":
        deps.pop()
    elif mutation == "duplicate":
        deps.append(dict(deps[0]))
    elif mutation == "skipped":
        deps[0]["skip_reason"] = "not_found"
    else:
        deps[0]["version"] = "0.0.1"
    write_json(audit, payload)
    result = load_module().audit_project(tmp_path, resolver_report=resolver, pip_audit_report=audit)
    assert result["status"] == "blocked"
    assert result["external_evidence"]["security_clean"] is False


def test_certification_requires_explicit_current_governance(tmp_path: Path) -> None:
    copy_project(tmp_path)
    path = tmp_path / "config/qlib_dependency_security_policy_v1.json"
    policy = json.loads(path.read_text(encoding="utf-8"))
    policy["governance_approval"]["lock_sha256"] = "0" * 64
    write_json(path, policy)
    assert load_module().audit_project(tmp_path)["policy_error"] == "current_graph_governance_approval_missing"


def test_known_vulnerable_graph_is_valid_blocked_policy(tmp_path: Path) -> None:
    copy_project(tmp_path)
    policy_path = tmp_path / "config/qlib_dependency_security_policy_v1.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    lock_path = tmp_path / "requirements-qlib-security.lock"
    lock_text = lock_path.read_text(encoding="utf-8").replace("mlflow==3.17.0", "mlflow==3.16.0")
    lock_path.write_text(lock_text, encoding="utf-8", newline="\n")
    policy.update({
        "policy_status": "BLOCKED", "p08_allowed": False,
        "qlib_security_gate_passed": False, "approved_security_clean_resolution_found": False,
        "p08_security_gate_remains_blocked": True,
        "expected_security_lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
    })
    policy["expected_packages"]["mlflow"] = "3.16.0"
    policy["p08_functional_regression"]["status"] = "blocked"
    write_json(policy_path, policy)
    report = load_module().audit_project(tmp_path)
    assert report["policy_valid"] is True
    assert report["policy_error"] is None
    assert report["reason"] == "qlib_security_blocked"
    assert report["candidate_evidence_valid"] is False
    assert report["p08_allowed"] is False


@pytest.mark.parametrize("key,value", [
    ("policy_status", []), ("expected_security_lock_package_count", True),
    ("security_advisories", []), ("p08_functional_regression", None),
])
def test_malformed_policy_is_not_a_valid_security_block(tmp_path: Path, key: str, value: Any) -> None:
    copy_project(tmp_path)
    path = tmp_path / "config/qlib_dependency_security_policy_v1.json"
    policy = json.loads(path.read_text(encoding="utf-8"))
    policy[key] = value
    write_json(path, policy)
    report = load_module().audit_project(tmp_path)
    assert report["policy_valid"] is False
    assert report["reason"] == "policy_invalid_or_incomplete"
    assert report["p08_allowed"] is False


def candidate_reports(root: Path) -> tuple[Path, Path, Path]:
    copy_project(root, certified=False)
    resolver, audit = clean_external_evidence(root)
    lock = load_module()._parse_hashed_lock(root / "requirements-qlib-security.lock")
    install = write_json(root / "install.json", {
        "environment": {"sys_platform": "linux", "python_version": "3.12"},
        "install": [{
            "metadata": {"name": name, "version": version},
            "download_info": {"archive_info": {"hashes": {"sha256": lock["package_hashes"][name][0]}}},
        } for name, version in lock["packages"].items()],
    })
    return resolver, audit, install


def test_complete_candidate_validation_does_not_authorize_p08(tmp_path: Path) -> None:
    resolver, audit, install = candidate_reports(tmp_path)
    result = load_module().audit_candidate_evidence(
        tmp_path, resolver_report=resolver, pip_audit_report=audit, hashed_install_report=install,
    )
    assert result["candidate_validation_status"] == "ok"
    assert result["status"] == "blocked"
    assert result["reason"] == "qlib_pending_certification"
    assert result["p08_allowed"] is False
    assert result["qlib_security_gate_passed"] is False
    assert result["external_evidence"]["audited_package_count"] == 189
    assert result["external_evidence"]["missing_packages"] == []
    assert result["hashed_install_evidence"]["package_count"] == 189


@pytest.mark.parametrize("package", ["packaging", "setuptools"])
@pytest.mark.parametrize("mutation", ["missing", "duplicate", "wrong_version", "skipped", "unidentifiable"])
def test_bootstrap_packages_cannot_escape_candidate_audit(
    tmp_path: Path, package: str, mutation: str,
) -> None:
    resolver, audit, install = candidate_reports(tmp_path)
    payload = json.loads(audit.read_text(encoding="utf-8"))
    dep = next(row for row in payload["dependencies"] if row["name"] == package)
    if mutation == "missing":
        payload["dependencies"].remove(dep)
    elif mutation == "duplicate":
        payload["dependencies"].append(dict(dep))
    elif mutation == "wrong_version":
        dep["version"] = "0.0.1"
    elif mutation == "skipped":
        dep["skip_reason"] = "not_audited"
    else:
        dep["name"] = None
    write_json(audit, payload)
    result = load_module().audit_candidate_evidence(
        tmp_path, resolver_report=resolver, pip_audit_report=audit, hashed_install_report=install,
    )
    assert result["candidate_validation_status"] == "blocked"
    assert result["p08_allowed"] is False
    if mutation == "missing":
        assert result["external_evidence"]["missing_packages"] == [package]


@pytest.mark.parametrize("mutation", ["missing_package", "duplicate_package", "wrong_archive_hash", "wrong_target"])
def test_installation_report_is_an_independent_prerequisite(tmp_path: Path, mutation: str) -> None:
    resolver, audit, install = candidate_reports(tmp_path)
    payload = json.loads(install.read_text(encoding="utf-8"))
    if mutation == "missing_package":
        payload["install"].pop()
    elif mutation == "duplicate_package":
        payload["install"].append(dict(payload["install"][0]))
    elif mutation == "wrong_archive_hash":
        payload["install"][0]["download_info"]["archive_info"]["hashes"]["sha256"] = "0" * 64
    else:
        payload["environment"]["sys_platform"] = "win32"
    write_json(install, payload)
    result = load_module().audit_candidate_evidence(
        tmp_path, resolver_report=resolver, pip_audit_report=audit, hashed_install_report=install,
    )
    assert result["candidate_validation_status"] == "blocked"
    assert result["hashed_install_evidence"]["status"] == "blocked"
    assert result["p08_allowed"] is False


def test_candidate_validation_requires_installation_report(tmp_path: Path) -> None:
    resolver, audit, _ = candidate_reports(tmp_path)
    result = load_module().audit_candidate_evidence(tmp_path, resolver_report=resolver, pip_audit_report=audit)
    assert result["candidate_validation_status"] == "blocked"
    assert result["candidate_validation_reason"] == "hashed_install_report_required"


def test_candidate_cli_success_is_not_authorization_success(tmp_path: Path) -> None:
    resolver, audit, install = candidate_reports(tmp_path)
    command = [
        sys.executable, str(SCRIPT), "--project-root", str(tmp_path),
        "--resolver-report", str(resolver), "--pip-audit-report", str(audit),
        "--hashed-install-report", str(install), "--json",
    ]
    candidate = subprocess.run([*command, "--candidate-validation"], capture_output=True, text=True, check=False)
    assert candidate.returncode == 0
    payload = json.loads(candidate.stdout)
    assert payload["candidate_validation_status"] == "ok"
    assert payload["status"] == "blocked"
    assert payload["p08_allowed"] is False
    assert payload["write_performed"] is False
    normal = subprocess.run(command, capture_output=True, text=True, check=False)
    assert normal.returncode == 1
    assert json.loads(normal.stdout)["p08_allowed"] is False
