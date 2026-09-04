from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "config" / "dev_branch_protection_policy_v1.json"


def evaluate_branch_payload(payload: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, Any]:
    protection = payload.get("protection") if isinstance(payload.get("protection"), Mapping) else {}
    checks = protection.get("required_status_checks") if isinstance(protection.get("required_status_checks"), Mapping) else {}
    enforcement = str(checks.get("enforcement_level", "off"))
    blockers: list[str] = []
    if payload.get("name") != policy.get("branch"):
        blockers.append("unexpected_branch")
    if payload.get("protected") is not True:
        blockers.append("branch_not_protected")
    if enforcement == "off":
        blockers.append("required_status_checks_not_enforced")
    return {
        "schema_version": "dev_branch_protection_audit_v1",
        "status": "ok" if not blockers else "blocked",
        "reason": "dev_branch_protection_enforced" if not blockers else "dev_branch_protection_incomplete",
        "branch": payload.get("name"),
        "protected": payload.get("protected") is True,
        "required_status_checks_enforcement": enforcement,
        "blockers": blockers,
        "changes_runtime": False,
        "changes_risk": False,
        "sends_orders": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
    }


def fetch_branch(repository: str, branch: str) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{repository}/branches/{branch}"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "smartcrypto-branch-protection-audit-v1"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


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
        payload = fetch_branch(args.repository, args.branch)
        report = evaluate_branch_payload(payload, policy)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        report = {
            "schema_version": "dev_branch_protection_audit_v1",
            "status": "blocked",
            "reason": "branch_protection_evidence_unavailable",
            "branch": args.branch,
            "protected": False,
            "required_status_checks_enforcement": "unknown",
            "blockers": [type(exc).__name__],
            "changes_runtime": False,
            "changes_risk": False,
            "sends_orders": False,
            "live_release_allowed": False,
            "canary_release_allowed": False,
        }
    print(json.dumps(report, sort_keys=True) if args.json else f"{report['status']}: {report['reason']}")
    return 1 if args.fail_on_unprotected and report["status"] != "ok" else 0


if __name__ == "__main__":
    raise SystemExit(main())
