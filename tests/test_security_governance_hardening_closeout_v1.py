from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_branch_protection_evaluator_fails_closed() -> None:
    module = _load("scripts/audit_dev_branch_protection_v1.py", "branch_protection_audit")
    policy = json.loads((ROOT / "config/dev_branch_protection_policy_v1.json").read_text())
    blocked = module.evaluate_branch_payload({"name": "dev", "protected": False, "protection": {"required_status_checks": {"enforcement_level": "off"}}}, policy)
    assert blocked["status"] == "blocked"
    assert "branch_not_protected" in blocked["blockers"]
    ok = module.evaluate_branch_payload({"name": "dev", "protected": True, "protection": {"required_status_checks": {"enforcement_level": "non_admins"}}}, policy)
    assert ok["status"] == "ok"


def test_review_registries_are_exact_and_safety_non_authoritative() -> None:
    boundary = json.loads((ROOT / "config/state_execution_boundary_review_registry_v1.json").read_text())
    exceptions = json.loads((ROOT / "config/operational_exception_review_registry_v1.json").read_text())
    assert boundary["reviewed_finding_count"] == 202
    assert exceptions["reviewed_finding_count"] == 137
    assert len(boundary["reviewed_finding_set_sha256"]) == 64
    assert len(exceptions["reviewed_finding_set_sha256"]) == 64
    assert boundary["safety"]["runtime_authority"] is False
    assert boundary["safety"]["financial_ledger_authority"] is False
    assert exceptions["non_waivable_severities"] == ["high", "critical"]


def test_paper_api_disabled_and_no_placeholder_credentials() -> None:
    payload = json.loads((ROOT / "freqtrade/user_data/config.paper.json").read_text())
    api = payload["api_server"]
    assert api["enabled"] is False
    assert api["listen_ip_address"] == "127.0.0.1"
    for key in ("username", "password", "ws_token", "jwt_secret_key"):
        assert key not in api


def test_live_example_has_no_writable_user_data_bind() -> None:
    text = (ROOT / "docker-compose.live.example.yml").read_text()
    assert "./freqtrade/user_data:/freqtrade/user_data" not in text
    assert "config.live.example.json:/freqtrade/user_data/config.live.example.json:ro" in text
    assert "./freqtrade/user_data/strategies:/freqtrade/user_data/strategies:ro" in text
    assert "tmpfs:" in text and "/freqtrade/user_data" in text


def test_ci_actions_are_pinned_to_full_shas() -> None:
    text = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in text
    assert "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065" in text
    assert "actions/checkout@v4" not in text
    assert "actions/setup-python@v5" not in text


def test_security_resolution_evidence_closes_qlib_dependency_vulnerability_without_authority() -> None:
    evidence = json.loads((ROOT / "config/security_resolution_evidence_v1.json").read_text())
    qlib = evidence["qlib"]
    assert evidence["evidence_status"] == "certified_clean"
    assert qlib["resolver_status"] == "resolved"
    assert qlib["pip_audit_exit_code"] == 0
    assert qlib["known_vulnerability_count"] == 0
    assert qlib["resolved_package_count"] == 190
    assert qlib["packages"]["cryptography"] == "50.0.0"
    assert qlib["packages"]["mlflow"] == "3.16.0"
    assert qlib["packages"]["pyarrow"] == "25.0.1"
    assert evidence["safety"]["operational_authority"] is False
    assert evidence["safety"]["runtime_updated"] is False
    assert evidence["safety"]["model_promotion_performed"] is False


def test_all_primary_python_and_freqtrade_images_are_immutable() -> None:
    expected = {
        "python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534",
        "python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea",
        "freqtradeorg/freqtrade:stable@sha256:7031bca43ed7668ebf421725dd5016acade6ef88b0771db3e08c96e6d19a42db",
    }
    docker_text = "\n".join(
        (ROOT / path).read_text()
        for path in (
            "docker/smartcrypto/Dockerfile",
            "docker/dashboard/Dockerfile",
            "docker/qlib/Dockerfile",
            "bitradex_realtime_candle_collector_v1/Dockerfile",
            "docker-compose.paper.yml",
            "docker-compose.live.example.yml",
        )
    )
    for image in expected:
        assert image in docker_text
    assert "FROM python:3.11-slim\n" not in docker_text
    assert "FROM python:3.12-slim\n" not in docker_text
    assert "freqtradeorg/freqtrade:stable\n" not in docker_text


def test_runtime_dependency_installs_are_require_hashes_and_no_upgrade() -> None:
    paths = (
        "docker/smartcrypto/Dockerfile",
        "docker/dashboard/Dockerfile",
        "docker/qlib/Dockerfile",
        "bitradex_realtime_candle_collector_v1/Dockerfile",
        ".github/workflows/ci.yml",
    )
    text = "\n".join((ROOT / path).read_text() for path in paths)
    assert "--require-hashes" in text
    assert "pip install --upgrade" not in text
    assert "pip install -r requirements-runtime.lock" not in text


def test_ci_does_not_short_circuit_feature_branch_on_external_h01() -> None:
    text = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "Audit dev branch protection governance" in text
    assert "Enforce dev branch protection governance on dev" in text
    assert "if: github.ref == 'refs/heads/dev'" in text
