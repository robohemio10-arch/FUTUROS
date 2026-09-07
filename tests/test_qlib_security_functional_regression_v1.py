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
)

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
    assert report["advisories_explicitly_enforced"] == [
        "GHSA-gqvg-gmmx-x4hm",
        "CVE-2026-69247",
    ]
    assert report["p08_allowed"] is (report["functional_status"] == "ci_certified")


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
        match="p08_allowed_inconsistent_with_functional_status",
    ):
        validate_policy_contract(policy, parse_security_lock(SECURITY_LOCK))
