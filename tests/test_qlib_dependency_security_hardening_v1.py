from __future__ import annotations

import hashlib
import importlib.util
import json
import re
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
CURRENT_LOCK_SHA256 = "89946862c34a816c649986cd7d8cc5269f8865e473e897f9710fb9baa281d871"
HISTORICAL_POLICY_EVIDENCE_SHA256 = (
    "933ccf7aed5c17a62cc617b5e659bbab5def548a7454c9622a6ed6698c31b11b"
)
HISTORICAL_RESOLUTION_EVIDENCE_SHA256 = (
    "32aba6f1ba5406fd9d0bd167f7ddf1c2fb76480b2c55ff14ac06fdd4794d8fa1"
)


def load_module() -> Any:
    module_path = (
        ROOT / "smartcrypto" / "learning" / "qlib_dependency_security_hardening" / "audit.py"
    )
    spec = importlib.util.spec_from_file_location(
        "qlib_dependency_security_hardening_test", module_path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def copy_project(root: Path) -> None:
    for source, relative in (
        (POLICY, "config/qlib_dependency_security_policy_v1.json"),
        (DIRECT_LOCK, "requirements-qlib.lock"),
        (SECURITY_LOCK, "requirements-qlib-security.lock"),
        (EVIDENCE, "config/security_resolution_evidence_v1.json"),
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def locked_packages_and_hashes() -> list[tuple[str, str, str]]:
    entries: list[tuple[str, str, str]] = []
    for block in re.split(
        r"(?=^[A-Za-z0-9_.-]+==)", SECURITY_LOCK.read_text(encoding="utf-8"), flags=re.MULTILINE
    ):
        pin = re.match(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)", block)
        if pin is None:
            continue
        digest = re.search(r"--hash=sha256:([a-f0-9]{64})", block)
        assert digest is not None
        entries.append((pin[1], pin[2], digest[1]))
    assert len(entries) == 190
    return entries


def clean_external_evidence(root: Path) -> tuple[Path, Path]:
    packages = locked_packages_and_hashes()
    resolver = write_json(
        root / "resolver.json",
        {
            "install": [
                {
                    "metadata": {"name": name, "version": version},
                    "download_info": {"archive_info": {"hashes": {"sha256": digest}}},
                }
                for name, version, digest in packages
            ]
        },
    )
    audit = write_json(
        root / "audit.json",
        {
            "dependencies": [
                {"name": name, "version": version, "vulns": []} for name, version, _ in packages
            ]
        },
    )
    return resolver, audit


def test_current_repository_candidate_is_clean_pending_independent_ci() -> None:
    module = load_module()
    report = module.audit_project(ROOT)

    assert report["status"] == "pending_certification"
    assert report["reason"] == "security_clean_candidate_pending_ci"
    assert report["blockers"] == []
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["candidate_evidence_valid"] is True
    assert report["approved_security_clean_resolution_found"] is False
    assert report["qlib_security_gate_passed"] is False
    assert report["p08_allowed"] is False
    assert report["operational_authority"] is False
    assert report["runtime_updated"] is False
    assert report["model_promotion_performed"] is False


def test_current_candidate_versions_and_full_graph_audit_are_not_ci_certification() -> None:
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))["qlib"]

    assert evidence["resolver_status"] == "resolved"
    assert evidence["security_status"] == "clean"
    assert evidence["pip_audit_exit_code"] == 0
    assert evidence["known_vulnerability_count"] == 0
    assert evidence["resolved_package_count"] == 190
    assert evidence["audited_dependency_count"] == 190
    assert evidence["hashed_lock_sha256"] == CURRENT_LOCK_SHA256
    assert evidence["ci_revalidation_required"] is True
    assert evidence["packages"]["pyqlib"] == "0.9.7"
    assert evidence["packages"]["mlflow"] == "3.17.0"
    assert evidence["packages"]["gitpython"] == "3.2.1"
    assert evidence["packages"]["pillow"] == "13.0.0"
    assert evidence["packages"]["certifi"] == "2026.10.19"
    assert evidence["packages"]["cryptography"] == "50.0.0"
    assert evidence["packages"]["pyarrow"] == "25.0.1"


def test_historical_certifications_are_preserved_exactly_but_do_not_authorize_candidate() -> None:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))

    def fingerprint(value: object) -> str:
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    assert fingerprint(policy["historical_certified_evidence"]) == HISTORICAL_POLICY_EVIDENCE_SHA256
    historical = evidence["historical_certifications"]["2026-10-06"]
    assert fingerprint(historical) == HISTORICAL_RESOLUTION_EVIDENCE_SHA256
    assert historical["functional_ci_certification"]["run_id"] == 37467299675
    assert historical["functional_ci_certification"]["lock_sha256"] != CURRENT_LOCK_SHA256
    assert policy["policy_status"] == "PENDING_CERTIFICATION"
    assert policy["p08_functional_regression"]["status"] == "pending_ci_certification"
    assert policy["p08_allowed"] is False
    assert policy["p08_security_gate_remains_blocked"] is True


def test_direct_and_full_locks_are_hash_locked() -> None:
    report = load_module().audit_project(ROOT)
    lock = report["lock"]

    assert lock["status"] == "ok"
    assert lock["direct"]["requirement_count"] == 1
    assert lock["direct"]["hash_locked"] is True
    assert lock["direct"]["specs"] == ["pyqlib==0.9.7"]
    assert lock["security_lock"]["requirement_count"] == 190
    assert lock["security_lock"]["hash_locked"] is True
    assert lock["security_lock"]["sha256"] == CURRENT_LOCK_SHA256
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
    path.write_text(
        path.read_text().replace("cryptography==50.0.0", "cryptography==49.0.0", 1),
        encoding="utf-8",
    )

    report = load_module().audit_project(tmp_path)
    assert report["status"] == "blocked"
    assert "qlib_lock_contract_mismatch" in report["blockers"]


def test_full_security_lock_hash_tamper_with_unchanged_versions_is_blocked(tmp_path: Path) -> None:
    copy_project(tmp_path)
    path = tmp_path / "requirements-qlib-security.lock"
    original = path.read_text(encoding="utf-8")
    modified, substitutions = re.subn(
        r"--hash=sha256:[a-f0-9]{64}", "--hash=sha256:" + "0" * 64, original, count=1
    )
    assert substitutions == 1
    path.write_text(modified, encoding="utf-8")

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

    assert report["status"] == "pending_certification"
    assert report["qlib_security_gate_passed"] is False
    assert report["p08_allowed"] is False
    assert report["external_evidence"]["status"] == "ok"
    assert report["external_evidence"]["security_clean"] is True
    assert report["external_evidence"]["anchor_packages_match"] is True
    assert report["external_evidence"]["expected_package_count"] == 190
    assert report["external_evidence"]["audited_package_count"] == 190


@pytest.mark.parametrize("source", ["resolver", "audit"])
def test_external_missing_package_cannot_be_reported_clean(tmp_path: Path, source: str) -> None:
    copy_project(tmp_path)
    resolver, audit = clean_external_evidence(tmp_path)
    path, key = (resolver, "install") if source == "resolver" else (audit, "dependencies")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[key].pop()
    write_json(path, payload)

    report = load_module().audit_project(tmp_path, resolver_report=resolver, pip_audit_report=audit)
    assert report["status"] == "blocked"
    assert report["external_evidence"]["security_clean"] is False
    assert report["external_evidence"]["reason"] == "external_evidence_graph_mismatch"


@pytest.mark.parametrize("source", ["resolver", "audit"])
def test_external_duplicate_package_cannot_mask_graph_coverage(tmp_path: Path, source: str) -> None:
    copy_project(tmp_path)
    resolver, audit = clean_external_evidence(tmp_path)
    path, key = (resolver, "install") if source == "resolver" else (audit, "dependencies")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[key].append(payload[key][0])
    write_json(path, payload)

    report = load_module().audit_project(tmp_path, resolver_report=resolver, pip_audit_report=audit)
    assert report["status"] == "blocked"
    assert report["external_evidence"]["security_clean"] is False
    assert "duplicate" in report["external_evidence"]["reason"]


def test_external_skipped_audit_package_is_not_zero_findings(tmp_path: Path) -> None:
    copy_project(tmp_path)
    resolver, audit = clean_external_evidence(tmp_path)
    payload = json.loads(audit.read_text(encoding="utf-8"))
    payload["dependencies"][0]["skip_reason"] = "distribution could not be audited"
    write_json(audit, payload)

    report = load_module().audit_project(tmp_path, resolver_report=resolver, pip_audit_report=audit)
    assert report["status"] == "blocked"
    assert report["external_evidence"]["security_clean"] is False
    assert "skip" in report["external_evidence"]["reason"]


@pytest.mark.parametrize("mutation", ["missing", "unlisted"])
def test_external_resolver_requires_download_hash_from_the_locked_release(
    tmp_path: Path, mutation: str
) -> None:
    copy_project(tmp_path)
    resolver, audit = clean_external_evidence(tmp_path)
    payload = json.loads(resolver.read_text(encoding="utf-8"))
    if mutation == "missing":
        del payload["install"][0]["download_info"]
    else:
        payload["install"][0]["download_info"]["archive_info"]["hashes"]["sha256"] = "0" * 64
    write_json(resolver, payload)

    report = load_module().audit_project(tmp_path, resolver_report=resolver, pip_audit_report=audit)
    assert report["status"] == "blocked"
    assert report["external_evidence"]["security_clean"] is False
    expected_reason = (
        "resolver_download_info_must_be_object"
        if mutation == "missing"
        else "resolver_archive_hash_not_in_lock:aiohappyeyeballs"
    )
    assert report["external_evidence"]["reason"] == expected_reason


def test_empty_audit_cannot_certify_a_full_resolver_graph(tmp_path: Path) -> None:
    copy_project(tmp_path)
    resolver, audit = clean_external_evidence(tmp_path)
    write_json(audit, {"dependencies": []})

    report = load_module().audit_project(tmp_path, resolver_report=resolver, pip_audit_report=audit)
    assert report["status"] == "blocked"
    assert report["external_evidence"]["security_clean"] is False
    assert report["external_evidence"]["reason"] == "external_evidence_graph_mismatch"


@pytest.mark.parametrize("mutation", ["missing", "null", "invalid"])
def test_absent_or_malformed_vulnerability_results_are_not_clean(
    tmp_path: Path, mutation: str
) -> None:
    copy_project(tmp_path)
    resolver, audit = clean_external_evidence(tmp_path)
    payload = json.loads(audit.read_text(encoding="utf-8"))
    dependency = payload["dependencies"][0]
    if mutation == "missing":
        del dependency["vulns"]
    else:
        dependency["vulns"] = None if mutation == "null" else "not audited"
    write_json(audit, payload)

    report = load_module().audit_project(tmp_path, resolver_report=resolver, pip_audit_report=audit)
    assert report["status"] == "blocked"
    assert report["external_evidence"]["security_clean"] is False
    assert report["external_evidence"]["reason"] == "dependency_vulns_must_be_array"


def test_external_unknown_finding_is_blocking(tmp_path: Path) -> None:
    copy_project(tmp_path)
    resolver, audit = clean_external_evidence(tmp_path)
    payload = json.loads(audit.read_text(encoding="utf-8"))
    payload["dependencies"][0]["vulns"] = [{"id": "UNKNOWN-NEW-FINDING", "fix_versions": []}]
    write_json(audit, payload)
    report = load_module().audit_project(tmp_path, resolver_report=resolver, pip_audit_report=audit)

    assert report["status"] == "blocked"
    assert report["external_evidence"]["finding_count"] == 1
    assert report["external_evidence"]["security_clean"] is False


def test_external_anchor_mismatch_is_blocking(tmp_path: Path) -> None:
    copy_project(tmp_path)
    resolver, audit = clean_external_evidence(tmp_path)
    payload = json.loads(resolver.read_text())
    mlflow = next(item for item in payload["install"] if item["metadata"]["name"] == "mlflow")
    mlflow["metadata"]["version"] = "3.15.1"
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
    assert report["status"] == "pending_certification"
    assert DIRECT_LOCK.read_bytes() == before


def test_write_report_restricted_to_data_reports(tmp_path: Path) -> None:
    copy_project(tmp_path)
    module = load_module()
    report = module.audit_project(tmp_path)
    with pytest.raises(ValueError, match="report_destination_outside_data_reports"):
        module.write_report_atomic(tmp_path, Path("outside.json"), report)


def test_write_report_atomic_preserves_pending_certification(tmp_path: Path) -> None:
    copy_project(tmp_path)
    module = load_module()
    report = module.audit_project(tmp_path)
    target = module.write_report_atomic(
        tmp_path, Path("data/reports/qlib_dependency_security_hardening_v1.json"), report
    )
    payload = json.loads(target.read_text())
    assert payload["status"] == "pending_certification"
    assert payload["qlib_security_gate_passed"] is False
    assert payload["p08_allowed"] is False
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


@pytest.mark.parametrize(
    "injected",
    [
        {"status": "ok"},
        {"p08_allowed": True},
        {"qlib_security_gate_passed": True},
        {"status": "ok", "p08_allowed": True, "qlib_security_gate_passed": True},
        {"foo": "harmless extra field"},
    ],
    ids=["status", "p08", "security_gate", "all_reserved", "unknown_field"],
)
def test_safety_cannot_override_report_status_or_authorize_gates(
    tmp_path: Path, injected: dict[str, str | bool]
) -> None:
    copy_project(tmp_path)
    policy = tmp_path / "config/qlib_dependency_security_policy_v1.json"
    payload = json.loads(policy.read_text(encoding="utf-8"))
    payload["safety"].update(injected)
    write_json(policy, payload)

    report = load_module().audit_project(tmp_path)
    assert report["status"] == "blocked"
    assert report["reason"] == "policy_invalid_or_incomplete"
    assert report["policy_error"] == "unexpected_safety_fields"
    assert report["policy_valid"] is False
    assert report["p08_allowed"] is False
    assert report["qlib_security_gate_passed"] is False
    assert report["approved_security_clean_resolution_found"] is False
    assert report["operational_authority"] is False
    assert report["live_trading_enabled"] is False


@pytest.mark.parametrize("missing", ["operational_authority", "paper_only", "live_trading_enabled"])
def test_missing_known_safety_flag_blocks_the_policy(tmp_path: Path, missing: str) -> None:
    copy_project(tmp_path)
    policy = tmp_path / "config/qlib_dependency_security_policy_v1.json"
    payload = json.loads(policy.read_text(encoding="utf-8"))
    del payload["safety"][missing]
    write_json(policy, payload)

    report = load_module().audit_project(tmp_path)
    assert report["status"] == "blocked"
    assert report["policy_error"] == "unexpected_safety_fields"
    assert report["p08_allowed"] is False
    assert report["qlib_security_gate_passed"] is False
    assert report["operational_authority"] is False


def test_auditor_source_is_static_and_offline() -> None:
    combined = (
        (ROOT / "smartcrypto/learning/qlib_dependency_security_hardening/audit.py")
        .read_text()
        .lower()
        + "\n"
        + SCRIPT.read_text().lower()
    )
    for marker in (
        "subprocess.run",
        "subprocess.popen",
        "requests.",
        "urllib.request",
        "httpx.",
        "import docker",
        "import ccxt",
        "pip install",
        "shell=true",
        "--ignore-vuln",
    ):
        assert marker not in combined


def test_cli_returns_success_json_without_writing() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(ROOT), "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(completed.stdout)
    assert completed.returncode == 0
    assert payload["status"] == "pending_certification"
    assert payload["candidate_evidence_valid"] is True
    assert payload["qlib_security_gate_passed"] is False
    assert payload["p08_allowed"] is False
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
    assert payload["status"] == "pending_certification"
    assert payload["candidate_evidence_valid"] is True
    assert payload["qlib_security_gate_passed"] is False
    assert payload["write_requested"] is True
    assert payload["write_performed"] is True
