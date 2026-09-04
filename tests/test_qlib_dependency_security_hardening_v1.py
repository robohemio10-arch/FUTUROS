\
from __future__ import annotations

import importlib.util
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


def clean_external_evidence(root: Path) -> tuple[Path, Path]:
    resolver = write_json(
        root / "resolver.json",
        {
            "install": [
                {"metadata": {"name": "pyqlib", "version": "0.9.7"}},
                {"metadata": {"name": "mlflow", "version": "3.16.0"}},
                {"metadata": {"name": "cryptography", "version": "50.0.0"}},
                {"metadata": {"name": "pyarrow", "version": "25.0.1"}},
            ]
        },
    )
    audit = write_json(root / "audit.json", {"dependencies": []})
    return resolver, audit


def test_current_repository_security_resolution_is_certified_clean_research_only() -> None:
    module = load_module()
    report = module.audit_project(ROOT)

    assert report["status"] == "ok"
    assert report["reason"] == "approved_security_clean_resolution_certified"
    assert report["blockers"] == []
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["approved_security_clean_resolution_found"] is True
    assert report["qlib_security_gate_passed"] is True
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
    assert evidence["resolved_package_count"] == 190
    assert evidence["packages"]["pyqlib"] == "0.9.7"
    assert evidence["packages"]["mlflow"] == "3.16.0"
    assert evidence["packages"]["cryptography"] == "50.0.0"
    assert evidence["packages"]["pyarrow"] == "25.0.1"


def test_direct_and_full_locks_are_hash_locked() -> None:
    report = load_module().audit_project(ROOT)
    lock = report["lock"]

    assert lock["status"] == "ok"
    assert lock["direct"]["requirement_count"] == 1
    assert lock["direct"]["hash_locked"] is True
    assert lock["direct"]["specs"] == ["pyqlib==0.9.7"]
    assert lock["security_lock"]["requirement_count"] == 190
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
    assert report["external_evidence"]["reason"] == "external_evidence_anchor_mismatch"


def test_default_execution_is_deterministic() -> None:
    module = load_module()
    assert module.audit_project(ROOT) == module.audit_project(ROOT)


def test_default_execution_does_not_write() -> None:
    before = DIRECT_LOCK.read_bytes()
    report = load_module().audit_project(ROOT)
    assert report["status"] == "ok"
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
    assert payload["status"] == "ok"
    assert payload["qlib_security_gate_passed"] is True
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
