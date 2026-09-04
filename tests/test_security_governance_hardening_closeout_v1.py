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
