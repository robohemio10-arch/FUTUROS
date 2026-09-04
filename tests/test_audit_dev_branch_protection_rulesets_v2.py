from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "audit_dev_branch_protection_v1.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("audit_dev_branch_protection_v1", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit = _load_module()

POLICY = {
    "branch": "dev",
    "required": {
        "protected": True,
        "required_status_checks_enforcement": "non_off",
        "allow_force_pushes": False,
        "allow_deletions": False,
    },
}


def _branch_payload(*, protected: bool = True, enforcement: str = "off") -> dict[str, object]:
    return {
        "name": "dev",
        "protected": protected,
        "protection": {
            "required_status_checks": {
                "enforcement_level": enforcement,
                "contexts": [],
                "checks": [],
            }
        },
    }


def _ruleset(
    *,
    enforcement: str = "active",
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    with_checks: bool = True,
    with_deletion: bool = True,
    with_force_push_block: bool = True,
) -> dict[str, object]:
    rules: list[dict[str, object]] = []
    if with_deletion:
        rules.append({"type": "deletion"})
    if with_force_push_block:
        rules.append({"type": "non_fast_forward"})
    if with_checks:
        rules.append(
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": False,
                    "do_not_enforce_on_create": False,
                    "required_status_checks": [
                        {
                            "context": "Compile, scan, test and build",
                            "integration_id": 15368,
                        }
                    ],
                },
            }
        )
    return {
        "id": 22307765,
        "name": "Protect dev",
        "target": "branch",
        "enforcement": enforcement,
        "conditions": {
            "ref_name": {
                "include": include if include is not None else ["refs/heads/dev"],
                "exclude": exclude if exclude is not None else [],
            }
        },
        "rules": rules,
    }


def test_classic_branch_protection_remains_supported() -> None:
    report = audit.evaluate_governance(_branch_payload(enforcement="non_admins"), [], POLICY)
    assert report["status"] == "ok"
    assert report["evidence_source"] == "classic_branch_protection"
    assert report["required_status_checks_enforcement"] == "non_admins"


def test_active_ruleset_satisfies_required_checks_when_classic_endpoint_is_off() -> None:
    report = audit.evaluate_governance(_branch_payload(), [_ruleset()], POLICY)
    assert report["status"] == "ok"
    assert report["evidence_source"] == "repository_ruleset"
    assert report["protected"] is True
    assert report["required_status_checks_enforcement"] == "ruleset_active"
    assert report["required_status_check_contexts"] == ["Compile, scan, test and build"]
    assert report["deletions_blocked"] is True
    assert report["force_pushes_blocked"] is True


def test_inactive_ruleset_is_rejected() -> None:
    report = audit.evaluate_governance(_branch_payload(), [_ruleset(enforcement="evaluate")], POLICY)
    assert report["status"] == "blocked"
    assert "active_ruleset_not_found" in report["blockers"]
    assert "required_status_checks_not_enforced" in report["blockers"]


def test_ruleset_for_different_branch_is_rejected() -> None:
    report = audit.evaluate_governance(
        _branch_payload(),
        [_ruleset(include=["refs/heads/main"])],
        POLICY,
    )
    assert report["status"] == "blocked"
    assert "active_ruleset_not_found" in report["blockers"]


def test_excluded_dev_ref_is_rejected() -> None:
    report = audit.evaluate_governance(
        _branch_payload(),
        [_ruleset(include=["refs/heads/*"], exclude=["refs/heads/dev"])],
        POLICY,
    )
    assert report["status"] == "blocked"
    assert "active_ruleset_not_found" in report["blockers"]


def test_ruleset_without_required_checks_is_rejected() -> None:
    report = audit.evaluate_governance(_branch_payload(), [_ruleset(with_checks=False)], POLICY)
    assert report["status"] == "blocked"
    assert "required_status_checks_not_enforced" in report["blockers"]


@pytest.mark.parametrize(
    ("ruleset", "expected_blocker"),
    [
        (_ruleset(with_deletion=False), "deletions_not_blocked"),
        (_ruleset(with_force_push_block=False), "force_pushes_not_blocked"),
    ],
)
def test_ruleset_preserves_policy_guards(ruleset: dict[str, object], expected_blocker: str) -> None:
    report = audit.evaluate_governance(_branch_payload(), [ruleset], POLICY)
    assert report["status"] == "blocked"
    assert expected_blocker in report["blockers"]


def test_main_uses_ruleset_fallback_and_preserves_safety(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(POLICY), encoding="utf-8")
    monkeypatch.setattr(audit, "fetch_branch", lambda repository, branch: _branch_payload())
    monkeypatch.setattr(audit, "fetch_rulesets", lambda repository: [_ruleset()])

    exit_code = audit.main(
        [
            "--repository",
            "robohemio10-arch/FUTUROS",
            "--branch",
            "dev",
            "--policy",
            str(policy_path),
            "--json",
            "--fail-on-unprotected",
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert report["status"] == "ok"
    assert report["evidence_source"] == "repository_ruleset"
    assert report["changes_runtime"] is False
    assert report["changes_risk"] is False
    assert report["sends_orders"] is False
    assert report["live_release_allowed"] is False
    assert report["canary_release_allowed"] is False
