from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import audit_qlib_security_functional_regression_v1 as functional_regression
from scripts.audit_qlib_security_functional_regression_v1 import (
    FunctionalRegressionError,
    parse_security_lock,
    validate_policy_contract,
    validate_static_contract,
)

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config/qlib_dependency_security_policy_v1.json"
SECURITY_LOCK = ROOT / "requirements-qlib-security.lock"
CURRENT_LOCK_SHA256 = "89946862c34a816c649986cd7d8cc5269f8865e473e897f9710fb9baa281d871"


def _policy() -> dict[str, object]:
    payload = json.loads(POLICY.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_repository_static_contract_enforces_advisories_and_p08_state() -> None:
    report = validate_static_contract(ROOT)

    assert report["lock_sha256"] == CURRENT_LOCK_SHA256
    assert report["advisories_explicitly_enforced"] == sorted(_policy()["security_advisories"])
    assert len(report["advisories_explicitly_enforced"]) == 69
    assert report["candidate_evidence_valid"] is True
    assert report["functional_status"] == "pending_ci_certification"
    assert report["p08_allowed"] is False


def test_current_lock_binds_the_full_candidate_graph() -> None:
    lock = parse_security_lock(SECURITY_LOCK)
    assert lock["sha256"] == CURRENT_LOCK_SHA256
    assert lock["package_count"] == 190
    assert lock["packages"] == _policy()["expected_packages"]
    assert lock["packages"]["pyqlib"] == "0.9.7"
    assert lock["packages"]["mlflow"] == "3.17.0"
    assert lock["packages"]["gitpython"] == "3.2.1"
    assert lock["packages"]["pillow"] == "13.0.0"


@pytest.mark.parametrize(
    "advisory_id",
    [
        "GHSA-gqvg-gmmx-x4hm",
        "CVE-2026-69247",
        "GHSA-j7qq-vr8c-xvw3",
        "GHSA-2wqp-q3c6-ccj5",
        "GHSA-26p8-2jq9-3vq9",
        "GHSA-m65p-9j7q-6w33",
        "GHSA-5639-2j2p-m4mx",
    ],
)
def test_missing_security_advisory_is_fail_closed(advisory_id: str) -> None:
    policy = copy.deepcopy(_policy())
    advisories = policy["security_advisories"]
    assert isinstance(advisories, dict)
    del advisories[advisory_id]

    with pytest.raises(FunctionalRegressionError, match=f"advisory:{advisory_id}"):
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


def test_historical_ci_run_cannot_approve_the_current_graph() -> None:
    policy = copy.deepcopy(_policy())
    policy["policy_status"] = "CERTIFIED"
    policy["ci_revalidation_required"] = False
    policy["approved_security_clean_resolution_found"] = True
    policy["qlib_security_gate_passed"] = True
    policy["p08_allowed"] = True
    policy["p08_security_gate_remains_blocked"] = False
    functional = policy["p08_functional_regression"]
    assert isinstance(functional, dict)
    functional.update(
        status="ci_certified",
        certified_workflow_run_id=37467299675,
        certified_proof_commit_sha="35d00079b76256449bd9f415ef078b789c293946",
    )
    policy["governance_approval"] = {
        "approved": True,
        "lock_sha256": "74f12c7dbcc069c61cfce98ce51385c5ce3da774b33cdf3db0318736a1b80075",
        "review_reference": "historical CI run 37467299675",
    }

    with pytest.raises(
        FunctionalRegressionError, match="current_graph_governance_approval_missing"
    ):
        validate_policy_contract(policy, parse_security_lock(SECURITY_LOCK))


def test_duplicate_lock_package_is_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.lock"
    original = SECURITY_LOCK.read_text(encoding="utf-8")
    digest = re.search(r"--hash=sha256:([a-f0-9]{64})", original)
    assert digest is not None
    path.write_text(
        original + f"\naiohappyeyeballs==2.7.1 --hash=sha256:{digest[1]}\n", encoding="utf-8"
    )

    with pytest.raises(
        FunctionalRegressionError, match="security_lock_duplicate_package:aiohappyeyeballs"
    ):
        parse_security_lock(path)


def test_missing_hash_is_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "unhashed.lock"
    original = SECURITY_LOCK.read_text(encoding="utf-8")
    modified, count = re.subn(
        r"^aiohappyeyeballs==.*?(?=^aiohttp==)",
        "aiohappyeyeballs==2.7.1\n",
        original,
        count=1,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert count == 1
    path.write_text(modified, encoding="utf-8")

    with pytest.raises(
        FunctionalRegressionError, match="security_lock_missing_hash:aiohappyeyeballs"
    ):
        parse_security_lock(path)


@pytest.mark.parametrize("flag", ["qlib_security_gate_bypassed", "p08_security_gate_bypassed"])
def test_policy_cannot_bypass_security_gates(flag: str) -> None:
    policy = copy.deepcopy(_policy())
    policy[flag] = True
    with pytest.raises(FunctionalRegressionError, match=f"unsafe_policy_flag:{flag}"):
        validate_policy_contract(policy, parse_security_lock(SECURITY_LOCK))


def test_installed_inventory_covers_every_locked_package_and_separates_image_extras(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packages = parse_security_lock(SECURITY_LOCK)["packages"]
    distributions = [
        SimpleNamespace(metadata={"Name": name}, version=version)
        for name, version in packages.items()
    ]
    distributions.extend(
        [
            SimpleNamespace(metadata={"Name": "pip"}, version="26.2.1"),
            SimpleNamespace(metadata={"Name": "smartcrypto"}, version="0.2.1"),
        ]
    )
    monkeypatch.setattr(
        functional_regression.importlib.metadata, "distributions", lambda: distributions
    )

    installed, extras = functional_regression._installed_inventory(packages)
    assert installed == packages
    assert len(installed) == 190
    assert set(extras) == {"pip", "smartcrypto"}
    assert extras["pip"]["version"] == "26.2.1"
    assert extras["smartcrypto"]["version"] == "0.2.1"
    assert all(item["qlib_lock_audit_coverage"] == "not_included" for item in extras.values())


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("missing", "installed_package_missing:aiohappyeyeballs"),
        ("duplicate", "installed_package_duplicate:ruamel-yaml"),
        ("mismatch", "installed_package_version_mismatch:aiohappyeyeballs"),
        ("unexpected", "installed_package_unexpected:streamlit"),
        ("wrong_local_package", "installed_package_unexpected:smartcrypto"),
        ("nameless", "installed_package_name_missing"),
    ],
)
def test_installed_inventory_fails_closed_for_incomplete_or_contaminated_graphs(
    monkeypatch: pytest.MonkeyPatch, mutation: str, reason: str
) -> None:
    packages = parse_security_lock(SECURITY_LOCK)["packages"]
    distributions = [
        SimpleNamespace(metadata={"Name": name}, version=version)
        for name, version in packages.items()
    ]
    if mutation == "missing":
        distributions.pop(0)
    elif mutation == "duplicate":
        distributions.append(
            SimpleNamespace(metadata={"Name": "RUAMEL_YAML"}, version=packages["ruamel-yaml"])
        )
    elif mutation == "mismatch":
        distributions[0].version = "0.0.0"
    elif mutation == "unexpected":
        distributions.append(SimpleNamespace(metadata={"Name": "streamlit"}, version="1.66.1"))
    elif mutation == "wrong_local_package":
        distributions.append(SimpleNamespace(metadata={"Name": "smartcrypto"}, version="9.9.9"))
    else:
        distributions[0].metadata = {}
    monkeypatch.setattr(
        functional_regression.importlib.metadata, "distributions", lambda: distributions
    )

    with pytest.raises(FunctionalRegressionError, match=reason):
        functional_regression._installed_inventory(packages)
