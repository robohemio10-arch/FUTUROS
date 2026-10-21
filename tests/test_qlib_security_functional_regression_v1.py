from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.audit_qlib_security_functional_regression_v1 import (
    FunctionalRegressionError,
    parse_security_lock,
    validate_policy_contract,
    validate_static_contract,
    _installed_versions,
    run_functional_regression,
)
from smartcrypto.learning.qlib_dependency_security_hardening.audit import REQUIRED_ADVISORIES

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config/qlib_dependency_security_policy_v1.json"
SECURITY_LOCK = ROOT / "requirements-qlib-security.lock"


def _policy() -> dict[str, object]:
    payload = json.loads(POLICY.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_repository_static_contract_enforces_advisories_and_p08_state() -> None:
    report = validate_static_contract(ROOT)

    assert report["lock_sha256"]
    assert report["advisories_explicitly_enforced"] == sorted(REQUIRED_ADVISORIES)
    assert report["p08_allowed"] is False
    assert report["policy_state"] == "PENDING_CERTIFICATION"


def test_missing_mlflow_advisory_is_fail_closed() -> None:
    policy = copy.deepcopy(_policy())
    advisories = policy["security_advisories"]
    assert isinstance(advisories, dict)
    del advisories["GHSA-gqvg-gmmx-x4hm"]

    with pytest.raises(FunctionalRegressionError, match="advisory:GHSA-gqvg-gmmx-x4hm"):
        validate_policy_contract(policy, parse_security_lock(SECURITY_LOCK))


def test_lock_identity_mismatch_is_fail_closed() -> None:
    policy = copy.deepcopy(_policy())
    policy["expected_security_lock_sha256"] = "0" * 64

    with pytest.raises(FunctionalRegressionError, match="policy_lock_sha_mismatch"):
        validate_policy_contract(policy, parse_security_lock(SECURITY_LOCK))


def test_p08_cannot_be_allowed_before_ci_certification() -> None:
    policy = copy.deepcopy(_policy())
    functional = policy["p08_functional_regression"]
    assert isinstance(functional, dict)
    functional["status"] = "pending_ci_certification"
    policy["p08_allowed"] = True
    policy["p08_security_gate_remains_blocked"] = False

    with pytest.raises(
        FunctionalRegressionError,
        match="policy_state_flag_mismatch:p08_allowed",
    ):
        validate_policy_contract(policy, parse_security_lock(SECURITY_LOCK))


@pytest.mark.parametrize("package", ["tornado", "mlflow", "gitpython", "pillow", "certifi", "aiohttp", "mako"])
@pytest.mark.parametrize("actual", [None, "0.0.1"])
def test_every_installed_security_pin_is_explicitly_verified(
    monkeypatch: pytest.MonkeyPatch, package: str, actual: str | None,
) -> None:
    import importlib.metadata

    def installed(name: str) -> str:
        if actual is None:
            raise importlib.metadata.PackageNotFoundError(name)
        return actual

    monkeypatch.setattr(importlib.metadata, "version", installed)
    expected = _policy()["expected_packages"]
    assert isinstance(expected, dict)
    with pytest.raises(FunctionalRegressionError, match="installed_package_(missing|version_mismatch)"):
        _installed_versions({package: expected[package]})


@pytest.mark.parametrize("advisory_id", sorted(REQUIRED_ADVISORIES))
def test_functional_contract_rejects_each_missing_advisory(advisory_id: str) -> None:
    policy = _policy()
    advisories = policy["security_advisories"]
    assert isinstance(advisories, dict)
    del advisories[advisory_id]
    with pytest.raises(FunctionalRegressionError, match="advisory:"):
        validate_policy_contract(policy, parse_security_lock(SECURITY_LOCK))


def test_blocked_policy_stops_before_imports(monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.audit_qlib_security_functional_regression_v1 as module

    monkeypatch.setattr(module, "validate_static_contract", lambda _: {"policy_state": "BLOCKED"})
    monkeypatch.setattr(module, "_installed_versions", lambda _: pytest.fail("import reached while blocked"))
    with pytest.raises(FunctionalRegressionError, match="qlib_security_blocked"):
        run_functional_regression(ROOT)


def test_missing_current_evidence_stops_functional_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.audit_qlib_security_functional_regression_v1 as module

    monkeypatch.setattr(module, "validate_static_contract", lambda _: {"policy_state": "PENDING_CERTIFICATION", "candidate_evidence_valid": False})
    monkeypatch.setattr(module, "_installed_versions", lambda _: pytest.fail("imports before evidence validation"))
    with pytest.raises(FunctionalRegressionError, match="current_graph_evidence_invalid"):
        run_functional_regression(ROOT)
