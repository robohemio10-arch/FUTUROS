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


def load_module() -> Any:
    module_path = (
        ROOT
        / "smartcrypto"
        / "learning"
        / "qlib_dependency_security_hardening"
        / "audit.py"
    )
    spec = importlib.util.spec_from_file_location(
        "qlib_dependency_security_hardening_test",
        module_path,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_text(root: Path, relative: str | Path, content: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return path


def copy_policy(root: Path) -> Path:
    target = root / "config" / "qlib_dependency_security_policy_v1.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(POLICY.read_bytes())
    return target


def write_expected_lock(root: Path) -> Path:
    return write_text(
        root,
        "requirements-qlib.lock",
        "# Research-only Qlib backend lock. Do not install in runtime executor images.\n"
        "pyqlib==0.9.7\n",
    )


def make_project(root: Path) -> None:
    copy_policy(root)
    write_expected_lock(root)


def test_current_repository_policy_is_blocked_fail_closed() -> None:
    module = load_module()
    report = module.audit_project(ROOT)

    assert report["status"] == "blocked"
    assert report["reason"] == "upstream_constraint_blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["approved_security_clean_resolution_found"] is False
    assert report["qlib_security_gate_passed"] is False


def test_current_resolution_records_cryptography_vulnerability() -> None:
    module = load_module()
    report = module.audit_project(ROOT)

    current = report["current_resolution"]
    assert current["packages"]["cryptography"] == "49.0.0"
    assert current["packages"]["mlflow"] == "3.15.1"
    assert current["packages"]["pyarrow"] == "25.0.1"
    assert current["known_vulnerability_count"] == 1
    assert current["security_status"] == "blocked"
    assert any(item["id"] == "PYSEC-2026-3552" for item in current["findings"])


def test_crypto50_fallback_is_not_accepted() -> None:
    module = load_module()
    report = module.audit_project(ROOT)

    fallback = report["cryptography_50_fallback"]
    assert fallback["packages"]["cryptography"] == "50.0.0"
    assert fallback["packages"]["mlflow"] == "3.2.0"
    assert fallback["packages"]["pyarrow"] == "21.0.0"
    assert fallback["known_vulnerability_count"] == 26
    assert fallback["finding_summary"]["mlflow"] == 25
    assert fallback["finding_summary"]["pyarrow"] == 1
    assert fallback["security_status"] == "blocked"


def test_pyarrow_fallback_finding_is_preserved() -> None:
    module = load_module()
    report = module.audit_project(ROOT)

    required = report["cryptography_50_fallback"]["required_findings"]
    assert any(
        item["package"] == "pyarrow"
        and item["version"] == "21.0.0"
        and item["id"] == "PYSEC-2026-113"
        for item in required
    )


def test_modern_mlflow_crypto50_is_incompatible() -> None:
    module = load_module()
    report = module.audit_project(ROOT)

    evidence = report["modern_mlflow_crypto50"]
    assert evidence["resolver_status"] == "incompatible"
    assert evidence["packages"]["mlflow"] == "3.15.0"
    assert evidence["packages"]["cryptography"] == "50.0.0"
    assert evidence["security_status"] == "blocked"


def test_resolver_success_does_not_imply_security_pass() -> None:
    module = load_module()
    report = module.audit_project(ROOT)

    assert report["current_resolution"]["resolver_status"] == "resolved"
    assert report["current_resolution"]["security_status"] == "blocked"
    assert report["qlib_security_gate_passed"] is False


def test_single_package_fix_does_not_imply_secure_graph() -> None:
    module = load_module()
    report = module.audit_project(ROOT)

    fallback = report["cryptography_50_fallback"]
    assert fallback["packages"]["cryptography"] == "50.0.0"
    assert fallback["known_vulnerability_count"] == 26
    assert report["qlib_security_gate_passed"] is False


def test_missing_policy_is_blocked(tmp_path: Path) -> None:
    module = load_module()
    write_expected_lock(tmp_path)

    report = module.audit_project(tmp_path)

    assert report["status"] == "blocked"
    assert report["reason"] == "policy_invalid_or_incomplete"


def test_invalid_policy_json_is_blocked(tmp_path: Path) -> None:
    module = load_module()
    write_expected_lock(tmp_path)
    write_text(
        tmp_path,
        "config/qlib_dependency_security_policy_v1.json",
        "{not-json}\n",
    )

    report = module.audit_project(tmp_path)

    assert report["status"] == "blocked"
    assert report["reason"] == "policy_invalid_or_incomplete"


def test_missing_lock_is_blocked(tmp_path: Path) -> None:
    module = load_module()
    copy_policy(tmp_path)

    report = module.audit_project(tmp_path)

    assert report["status"] == "blocked"
    assert report["reason"] == "qlib_lock_contract_mismatch"
    assert report["lock"]["reason"] == "requirements_qlib_lock_missing"


def test_changed_lock_hash_is_blocked(tmp_path: Path) -> None:
    module = load_module()
    copy_policy(tmp_path)
    write_text(
        tmp_path,
        "requirements-qlib.lock",
        "# changed comment invalidates the certified lock identity\npyqlib==0.9.7\n",
    )

    report = module.audit_project(tmp_path)

    assert report["status"] == "blocked"
    assert report["lock"]["hash_matches_policy"] is False


def test_changed_direct_pin_is_blocked(tmp_path: Path) -> None:
    module = load_module()
    copy_policy(tmp_path)
    write_text(
        tmp_path,
        "requirements-qlib.lock",
        "# Research-only Qlib backend lock. Do not install in runtime executor images.\n"
        "pyqlib==0.9.6\n",
    )

    report = module.audit_project(tmp_path)

    assert report["status"] == "blocked"
    assert report["lock"]["direct_requirements_match_policy"] is False


def test_expected_lock_contract_passes_structural_check(tmp_path: Path) -> None:
    module = load_module()
    make_project(tmp_path)

    report = module.audit_project(tmp_path)

    assert report["lock"]["status"] == "ok"
    assert report["lock"]["hash_matches_policy"] is True
    assert report["lock"]["direct_requirements_match_policy"] is True
    assert report["status"] == "blocked"


def test_crlf_lock_has_same_canonical_identity(tmp_path: Path) -> None:
    module = load_module()
    copy_policy(tmp_path)
    lock = tmp_path / "requirements-qlib.lock"
    lock.write_bytes(
        b"# Research-only Qlib backend lock. Do not install in runtime executor images.\r\n"
        b"pyqlib==0.9.7\r\n"
    )

    report = module.audit_project(tmp_path)

    assert report["lock"]["status"] == "ok"
    assert report["lock"]["hash_matches_policy"] is True
    assert report["lock"]["direct_requirements_match_policy"] is True


def test_default_execution_is_deterministic(tmp_path: Path) -> None:
    module = load_module()
    make_project(tmp_path)

    first = module.audit_project(tmp_path)
    second = module.audit_project(tmp_path)

    assert first == second
    assert first["policy_sha256"]


def test_default_execution_does_not_write_reports(tmp_path: Path) -> None:
    module = load_module()
    make_project(tmp_path)

    module.audit_project(tmp_path)

    assert not (tmp_path / "data").exists()


def test_write_report_is_restricted_to_data_reports(tmp_path: Path) -> None:
    module = load_module()
    make_project(tmp_path)
    report = module.audit_project(tmp_path)

    with pytest.raises(ValueError, match="report_destination_outside_data_reports"):
        module.write_report_atomic(tmp_path, Path("outside.json"), report)


def test_write_report_is_atomic_and_json_valid(tmp_path: Path) -> None:
    module = load_module()
    make_project(tmp_path)
    report = module.audit_project(tmp_path)

    target = module.write_report_atomic(
        tmp_path,
        Path("data/reports/qlib_dependency_security_hardening_v1.json"),
        report,
    )

    assert target.is_file()
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["status"] == "blocked"
    assert not list(target.parent.glob("*.tmp"))


def test_incomplete_external_evidence_pair_is_blocked(tmp_path: Path) -> None:
    module = load_module()
    make_project(tmp_path)
    resolver = write_text(tmp_path, "resolver.json", json.dumps({"install": []}))

    report = module.audit_project(tmp_path, resolver_report=resolver)

    assert report["external_evidence"]["status"] == "blocked"
    assert report["external_evidence"]["reason"] == "incomplete_external_evidence_pair"


def test_external_clean_evidence_cannot_self_approve_policy(tmp_path: Path) -> None:
    module = load_module()
    make_project(tmp_path)
    resolver = write_text(
        tmp_path,
        "resolver.json",
        json.dumps(
            {
                "install": [
                    {"metadata": {"name": "pyqlib", "version": "0.9.7"}},
                    {"metadata": {"name": "mlflow", "version": "99.0.0"}},
                    {"metadata": {"name": "cryptography", "version": "99.0.0"}},
                    {"metadata": {"name": "pyarrow", "version": "99.0.0"}},
                ]
            }
        ),
    )
    audit = write_text(tmp_path, "audit.json", json.dumps({"dependencies": []}))

    report = module.audit_project(
        tmp_path,
        resolver_report=resolver,
        pip_audit_report=audit,
    )

    assert report["external_evidence"]["security_clean"] is True
    assert report["status"] == "blocked"
    assert report["reason"] == "security_clean_evidence_requires_separate_policy_approval"
    assert report["qlib_security_gate_passed"] is False


def test_external_unknown_finding_is_blocking(tmp_path: Path) -> None:
    module = load_module()
    make_project(tmp_path)
    resolver = write_text(
        tmp_path,
        "resolver.json",
        json.dumps(
            {
                "install": [
                    {"metadata": {"name": "pyqlib", "version": "0.9.7"}},
                    {"metadata": {"name": "mlflow", "version": "3.15.1"}},
                ]
            }
        ),
    )
    audit = write_text(
        tmp_path,
        "audit.json",
        json.dumps(
            {
                "dependencies": [
                    {
                        "name": "some-package",
                        "version": "1.0.0",
                        "vulns": [{"id": "UNKNOWN-NEW-FINDING", "fix_versions": []}],
                    }
                ]
            }
        ),
    )

    report = module.audit_project(
        tmp_path,
        resolver_report=resolver,
        pip_audit_report=audit,
    )

    assert report["external_evidence"]["finding_count"] == 1
    assert report["external_evidence"]["security_clean"] is False
    assert report["status"] == "blocked"


def test_safety_flags_are_fail_closed() -> None:
    module = load_module()
    report = module.audit_project(ROOT)

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
    module_source = (
        ROOT
        / "smartcrypto"
        / "learning"
        / "qlib_dependency_security_hardening"
        / "audit.py"
    ).read_text(encoding="utf-8").lower()
    cli_source = SCRIPT.read_text(encoding="utf-8").lower()
    combined = module_source + "\n" + cli_source

    forbidden = (
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
        "--no-deps",
    )
    for marker in forbidden:
        assert marker not in combined


def test_cli_returns_blocked_json_and_nonzero_exit() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project-root",
            str(ROOT),
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    payload = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert payload["status"] == "blocked"
    assert payload["reason"] == "upstream_constraint_blocked"
    assert payload["write_requested"] is False
    assert payload["write_performed"] is False


def test_cli_default_does_not_modify_requirements_lock() -> None:
    before = (ROOT / "requirements-qlib.lock").read_bytes()

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project-root",
            str(ROOT),
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    after = (ROOT / "requirements-qlib.lock").read_bytes()
    assert completed.returncode == 1
    assert before == after


def test_cli_persisted_report_contains_truthful_success_write_audit(tmp_path: Path) -> None:
    make_project(tmp_path)

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
    persisted_path = (
        tmp_path
        / "data"
        / "reports"
        / "qlib_dependency_security_hardening_v1.json"
    )
    persisted = json.loads(persisted_path.read_text(encoding="utf-8"))

    assert completed.returncode == 1
    assert payload["write_requested"] is True
    assert payload["write_performed"] is True
    assert persisted["write_requested"] is True
    assert persisted["write_performed"] is True
    assert persisted["report_path"] == "data/reports/qlib_dependency_security_hardening_v1.json"


def test_cli_rejects_write_outside_data_reports(tmp_path: Path) -> None:
    make_project(tmp_path)

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project-root",
            str(tmp_path),
            "--write-report",
            "outside.json",
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    payload = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert payload["status"] == "blocked"
    assert payload["reason"] == "report_write_failed"
    assert payload["write_performed"] is False


def test_policy_hash_is_lineage_evidence() -> None:
    module = load_module()
    report = module.audit_project(ROOT)

    assert isinstance(report["policy_sha256"], str)
    assert len(report["policy_sha256"]) == 64
    int(report["policy_sha256"], 16)


def test_all_semantic_invariants_are_true() -> None:
    module = load_module()
    report = module.audit_project(ROOT)

    assert report["semantic_invariants"]
    assert all(report["semantic_invariants"].values())
