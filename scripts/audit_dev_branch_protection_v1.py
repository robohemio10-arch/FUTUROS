from __future__ import annotations

import argparse
import fnmatch
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "config" / "dev_branch_protection_policy_v1.json"
API_ROOT = "https://api.github.com"
USER_AGENT = "smartcrypto-branch-protection-audit-v1"


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return ()


def evaluate_branch_payload(payload: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, Any]:
    protection = _mapping(payload.get("protection"))
    checks = _mapping(protection.get("required_status_checks"))
    enforcement = str(checks.get("enforcement_level", "off"))
    blockers: list[str] = []
    if payload.get("name") != policy.get("branch"):
        blockers.append("unexpected_branch")
    if payload.get("protected") is not True:
        blockers.append("branch_not_protected")
    if enforcement == "off":
        blockers.append("required_status_checks_not_enforced")
    return _build_report(
        branch=payload.get("name"),
        protected=payload.get("protected") is True,
        enforcement=enforcement,
        blockers=blockers,
        evidence_source="classic_branch_protection",
    )


def _ref_matches(pattern: str, ref: str) -> bool:
    return pattern == ref or fnmatch.fnmatchcase(ref, pattern)


def _ruleset_applies_to_branch(ruleset: Mapping[str, Any], branch: str) -> bool:
    conditions = _mapping(ruleset.get("conditions"))
    ref_name = _mapping(conditions.get("ref_name"))
    include = [str(value) for value in _sequence(ref_name.get("include"))]
    exclude = [str(value) for value in _sequence(ref_name.get("exclude"))]
    branch_ref = f"refs/heads/{branch}"
    if not include or not any(_ref_matches(pattern, branch_ref) for pattern in include):
        return False
    return not any(_ref_matches(pattern, branch_ref) for pattern in exclude)


def _ruleset_requirements(ruleset: Mapping[str, Any]) -> tuple[bool, bool, bool, list[str]]:
    required_checks = False
    blocks_deletion = False
    blocks_force_push = False
    contexts: list[str] = []
    for raw_rule in _sequence(ruleset.get("rules")):
        rule = _mapping(raw_rule)
        rule_type = str(rule.get("type", ""))
        if rule_type == "deletion":
            blocks_deletion = True
        elif rule_type == "non_fast_forward":
            blocks_force_push = True
        elif rule_type == "required_status_checks":
            parameters = _mapping(rule.get("parameters"))
            raw_checks = _sequence(parameters.get("required_status_checks"))
            contexts.extend(
                str(_mapping(check).get("context", "")).strip()
                for check in raw_checks
                if str(_mapping(check).get("context", "")).strip()
            )
            required_checks = bool(contexts)
    return required_checks, blocks_deletion, blocks_force_push, contexts


def evaluate_rulesets_payload(
    rulesets: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    branch = str(policy.get("branch", "dev"))
    required = _mapping(policy.get("required"))
    require_no_deletions = required.get("allow_deletions") is False
    require_no_force_pushes = required.get("allow_force_pushes") is False
    applicable: list[Mapping[str, Any]] = [
        ruleset
        for ruleset in rulesets
        if str(ruleset.get("target", "")) == "branch"
        and str(ruleset.get("enforcement", "")) == "active"
        and _ruleset_applies_to_branch(ruleset, branch)
    ]

    required_checks = False
    blocks_deletion = not require_no_deletions
    blocks_force_push = not require_no_force_pushes
    contexts: list[str] = []
    ruleset_ids: list[int | str] = []
    ruleset_names: list[str] = []
    for ruleset in applicable:
        has_checks, has_deletion, has_force_push, rule_contexts = _ruleset_requirements(ruleset)
        required_checks = required_checks or has_checks
        blocks_deletion = blocks_deletion or has_deletion
        blocks_force_push = blocks_force_push or has_force_push
        contexts.extend(rule_contexts)
        ruleset_ids.append(ruleset.get("id", "unknown"))
        ruleset_names.append(str(ruleset.get("name", "")))

    blockers: list[str] = []
    if not applicable:
        blockers.append("active_ruleset_not_found")
    if not required_checks:
        blockers.append("required_status_checks_not_enforced")
    if require_no_deletions and not blocks_deletion:
        blockers.append("deletions_not_blocked")
    if require_no_force_pushes and not blocks_force_push:
        blockers.append("force_pushes_not_blocked")

    report = _build_report(
        branch=branch,
        protected=bool(applicable),
        enforcement="ruleset_active" if required_checks else "off",
        blockers=blockers,
        evidence_source="repository_ruleset",
    )
    report.update(
        {
            "ruleset_ids": ruleset_ids,
            "ruleset_names": ruleset_names,
            "required_status_check_contexts": sorted(set(contexts)),
            "deletions_blocked": blocks_deletion,
            "force_pushes_blocked": blocks_force_push,
        }
    )
    return report


def _build_report(
    *,
    branch: object,
    protected: bool,
    enforcement: str,
    blockers: list[str],
    evidence_source: str,
) -> dict[str, Any]:
    return {
        "schema_version": "dev_branch_protection_audit_v1",
        "status": "ok" if not blockers else "blocked",
        "reason": "dev_branch_protection_enforced" if not blockers else "dev_branch_protection_incomplete",
        "branch": branch,
        "protected": protected,
        "required_status_checks_enforcement": enforcement,
        "evidence_source": evidence_source,
        "blockers": blockers,
        "changes_runtime": False,
        "changes_risk": False,
        "sends_orders": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
    }


def _request_headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _fetch_json(url: str) -> Any:
    request = urllib.request.Request(url, headers=_request_headers())
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_branch(repository: str, branch: str) -> dict[str, Any]:
    payload = _fetch_json(f"{API_ROOT}/repos/{repository}/branches/{branch}")
    if not isinstance(payload, dict):
        raise json.JSONDecodeError("branch payload must be an object", "", 0)
    return payload


def fetch_rulesets(repository: str) -> list[dict[str, Any]]:
    summary_payload = _fetch_json(f"{API_ROOT}/repos/{repository}/rulesets")
    if not isinstance(summary_payload, list):
        raise json.JSONDecodeError("rulesets payload must be a list", "", 0)
    details: list[dict[str, Any]] = []
    for summary in summary_payload:
        if not isinstance(summary, Mapping):
            continue
        ruleset_id = summary.get("id")
        if ruleset_id is None:
            continue
        detail = _fetch_json(f"{API_ROOT}/repos/{repository}/rulesets/{ruleset_id}")
        if isinstance(detail, dict):
            details.append(detail)
    return details


def evaluate_governance(
    branch_payload: Mapping[str, Any],
    rulesets: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    classic_report = evaluate_branch_payload(branch_payload, policy)
    if classic_report["status"] == "ok":
        return classic_report
    ruleset_report = evaluate_rulesets_payload(rulesets, policy)
    if ruleset_report["status"] == "ok":
        return ruleset_report
    combined_blockers = sorted(set(classic_report["blockers"] + ruleset_report["blockers"]))
    report = dict(ruleset_report)
    report["protected"] = bool(classic_report["protected"] or ruleset_report["protected"])
    report["blockers"] = combined_blockers
    return report


def _evidence_unavailable_report(branch: str, blocker: str) -> dict[str, Any]:
    return {
        "schema_version": "dev_branch_protection_audit_v1",
        "status": "blocked",
        "reason": "branch_protection_evidence_unavailable",
        "branch": branch,
        "protected": False,
        "required_status_checks_enforcement": "unknown",
        "evidence_source": "unavailable",
        "blockers": [blocker],
        "changes_runtime": False,
        "changes_risk": False,
        "sends_orders": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--branch", default="dev")
    parser.add_argument("--policy", default=str(DEFAULT_POLICY))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--fail-on-unprotected", action="store_true")
    args = parser.parse_args(argv)
    policy = json.loads(Path(args.policy).read_text(encoding="utf-8"))
    try:
        branch_payload = fetch_branch(args.repository, args.branch)
        classic_report = evaluate_branch_payload(branch_payload, policy)
        if classic_report["status"] == "ok":
            report = classic_report
        else:
            rulesets = fetch_rulesets(args.repository)
            report = evaluate_governance(branch_payload, rulesets, policy)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        report = _evidence_unavailable_report(args.branch, type(exc).__name__)
    print(json.dumps(report, sort_keys=True) if args.json else f"{report['status']}: {report['reason']}")
    return 1 if args.fail_on_unprotected and report["status"] != "ok" else 0


if __name__ == "__main__":
    raise SystemExit(main())
