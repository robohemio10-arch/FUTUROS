from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/qlib-candidate-validation.yml"


def workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_candidate_workflow_is_independent_and_read_only() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    data = workflow()
    events = data.get("on", data.get(True))
    assert isinstance(events, dict)
    assert "pull_request" in events
    assert "pull_request_target" not in events
    assert data["permissions"] == {"contents": "read"}
    assert "requirements-runtime.lock" not in text
    assert "--ignore-vuln" not in text
    assert "continue-on-error" not in text
    assert re.search(r"\$\{\{\s*secrets\.", text) is None
    checkout = data["jobs"]["validate-candidate"]["steps"][0]
    assert checkout["with"]["ref"] == "${{ github.event.pull_request.head.sha || github.sha }}"
    assert checkout["with"]["persist-credentials"] is False
    for step in data["jobs"]["validate-candidate"]["steps"]:
        if "uses" in step:
            assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", step["uses"])


def test_audit_tools_use_a_separate_hash_locked_environment() -> None:
    data = workflow()
    step = next(s for s in data["jobs"]["validate-candidate"]["steps"] if s["name"].startswith("Prepare independent"))
    assert 'python -m venv "$RUNNER_TEMP/qlib-auditor"' in step["run"]
    assert '"$RUNNER_TEMP/qlib-auditor/bin/python" -m pip install --disable-pip-version-check --require-hashes -r requirements-dev.lock' in step["run"]
    assert '"$RUNNER_TEMP/qlib-auditor/bin/python" -m pip install --disable-pip-version-check --require-hashes --no-deps -r requirements-build.lock' in step["run"]
    assert '"$RUNNER_TEMP/qlib-auditor/bin/python" -m pip check' in step["run"]
    assert step["run"].index("-r requirements-dev.lock") < step["run"].index("-r requirements-build.lock") < step["run"].index("-m pip check")
    assert step["env"]["CANDIDATE_HEAD_SHA"] == "${{ github.event.pull_request.head.sha || github.sha }}"
    assert 'head != os.environ["CANDIDATE_HEAD_SHA"]' in step["run"]
    assert "candidate_head_mismatch" in step["run"]


def test_hash_locked_build_backend_is_installed_before_qlib_resolution() -> None:
    data = workflow()
    steps = data["jobs"]["validate-candidate"]["steps"]
    tools = next(step for step in steps if step["name"].startswith("Prepare independent"))
    resolver = next(step for step in steps if step["name"].startswith("Resolve the complete"))
    assert steps.index(tools) < steps.index(resolver)
    events = data.get("on", data.get(True))
    assert "requirements-dev.lock" in events["pull_request"]["paths"]
    assert "requirements-build.lock" in events["pull_request"]["paths"]
    assert "--no-build-isolation" in resolver["run"]


def test_exact_lock_audit_requires_resolution_installation_and_complete_coverage() -> None:
    data = workflow()
    steps = data["jobs"]["validate-candidate"]["steps"]
    resolver = next(s["run"] for s in steps if s["name"].startswith("Resolve the complete"))
    build = next(s["run"] for s in steps if s["name"].startswith("Build existing Qlib"))
    audit = next(s["run"] for s in steps if s["name"].startswith("Audit every exact"))
    coverage = next(s["run"] for s in steps if s["name"].startswith("Require exact coverage"))
    assert "docker/qlib/Dockerfile" in build
    assert "--require-hashes -r requirements-qlib-security.lock" in build
    assert "--ignore-installed --report /tmp/qlib-install-report.json" in build
    for flag in ("--dry-run", "--ignore-installed", "--no-build-isolation", "--require-hashes"):
        assert flag in resolver
    assert '-r requirements-qlib-security.lock' in resolver
    assert "--no-deps" not in resolver
    for flag in ("--require-hashes", "--disable-pip", "--strict", "--format json"):
        assert flag in audit
    assert "--no-deps" not in audit
    assert steps.index(next(s for s in steps if s.get("run") == resolver)) < steps.index(next(s for s in steps if s.get("run") == audit))
    for flag in ("--candidate-validation", "--hashed-install-report", "--resolver-report", "--pip-audit-report"):
        assert flag in coverage
    assert '--resolver-report "$RUNNER_TEMP/qlib-candidate-evidence/qlib-resolver-report.json"' in coverage
    assert '--hashed-install-report "$RUNNER_TEMP/qlib-candidate-evidence/qlib-install-report.json"' in coverage
    assert "--write-report" not in coverage


def test_functional_probe_is_offline_and_never_certifies_or_authorizes() -> None:
    data = workflow()
    step = next(s for s in data["jobs"]["validate-candidate"]["steps"] if s["name"].startswith("Validate the synthetic Qlib"))
    for marker in ("--network none", "--read-only", "--user 10001:10001", "readonly",
                   'report.get("p08_allowed") is not False', 'report["security"]["exact_certified_graph"] is not False',
                   'report["security"]["current_graph_ci_certified"] is not False',
                   'report["security"]["qlib_security_gate_bypassed"] is not False',
                   'report["safety"]["operational_authority"] is not False',
                   'policy["policy_status"] != "PENDING_CERTIFICATION"',
                   'report["audited_lock_package_count"] != policy["expected_security_lock_package_count"]'):
        assert marker in step["run"]
    assert data["env"]["LIVE_ENABLED"] == "false"
    assert data["env"]["ORDER_SUBMISSION_ENABLED"] == "false"
    assert data["env"]["REAL_ORDER_SUBMISSION_ENABLED"] == "false"


def test_candidate_evidence_is_uploaded_even_on_a_failed_gate() -> None:
    steps = workflow()["jobs"]["validate-candidate"]["steps"]
    upload = next(step for step in steps if step["name"].startswith("Upload candidate"))
    assert upload["if"] == "always()"
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["path"] == "${{ runner.temp }}/qlib-candidate-evidence"


@pytest.mark.parametrize(
    ("section", "key", "value", "accepted"),
    [
        ("report", "status", "ok", True),
        ("report", "status", "blocked", False),
        ("report", "p08_allowed", True, False),
        ("report", "audited_lock_package_count", 189, False),
        ("security", "exact_certified_graph", True, False),
        ("security", "current_graph_ci_certified", True, False),
        ("security", "qlib_security_gate_bypassed", True, False),
        ("safety", "operational_authority", True, False),
        ("policy", "policy_status", "CI_CERTIFIED", False),
    ],
)
def test_functional_evidence_guard_rejects_authorization_and_coverage_drift(
    tmp_path: Path, section: str, key: str, value: str | bool | int, accepted: bool,
) -> None:
    step = next(
        step for step in workflow()["jobs"]["validate-candidate"]["steps"]
        if step["name"].startswith("Validate the synthetic Qlib")
    )
    snippet = re.search(r"python - <<'PY'\n(.*?)^PY$", step["run"], re.M | re.S)
    assert snippet is not None
    report: dict[str, Any] = {
        "status": "ok", "p08_allowed": False, "audited_lock_package_count": 190,
        "security": {"exact_certified_graph": False, "current_graph_ci_certified": False,
                     "qlib_security_gate_bypassed": False},
        "safety": {"operational_authority": False},
    }
    policy: dict[str, Any] = {"policy_status": "PENDING_CERTIFICATION", "expected_security_lock_package_count": 190}
    target = policy if section == "policy" else report if section == "report" else report[section]
    target[key] = value
    evidence = tmp_path / "qlib-candidate-evidence"
    evidence.mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "config/qlib_dependency_security_policy_v1.json").write_text(json.dumps(policy), encoding="utf-8")
    (evidence / "qlib-functional.stdout").write_text(json.dumps(report), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-c", snippet.group(1)], cwd=tmp_path,
        env={**os.environ, "RUNNER_TEMP": str(tmp_path)}, capture_output=True, text=True, check=False,
    )
    assert (result.returncode == 0) is accepted, result.stderr
    assert (evidence / "qlib-functional.json").exists() is accepted
    if not accepted:
        assert "candidate_functional_regression_or_authorization_contract_failed" in result.stderr


def test_embedded_python_is_syntactically_valid() -> None:
    steps = workflow()["jobs"]["validate-candidate"]["steps"]
    scripts = [s["run"] for s in steps if "run" in s]
    snippets = [code for script in scripts for code in re.findall(r"python - <<'PY'\n(.*?)^PY$", script, re.M | re.S)]
    assert len(snippets) == 3
    for code in snippets:
        ast.parse(code)
