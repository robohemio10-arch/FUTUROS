from __future__ import annotations

import re
import ast
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/qlib-candidate-validation.yml"


def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_candidate_workflow_is_independent_and_read_only() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    data = workflow()
    events = data.get("on", data.get(True))
    assert isinstance(events, dict)
    assert "pull_request" in events
    assert "pull_request_target" not in events
    assert data["permissions"] == {"contents": "read"}
    assert "requirements-dev.lock" not in text
    assert "requirements-runtime.lock" not in text
    assert "--ignore-vuln" not in text
    assert "continue-on-error" not in text
    assert re.search(r"\$\{\{\s*secrets\.", text) is None
    for step in data["jobs"]["validate-candidate"]["steps"]:
        if "uses" in step:
            assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", step["uses"])


def test_exact_lock_audit_does_not_replace_full_installation() -> None:
    data = workflow()
    steps = data["jobs"]["validate-candidate"]["steps"]
    build = next(s["run"] for s in steps if s["name"].startswith("Build existing Qlib"))
    audit = next(s["run"] for s in steps if s["name"].startswith("Audit every exact"))
    coverage = next(s["run"] for s in steps if s["name"].startswith("Require exact coverage"))
    assert "docker/qlib/Dockerfile" in build
    assert "--require-hashes -r requirements-qlib-security.lock" in build
    assert "--ignore-installed --report /tmp/qlib-install-report.json" in build
    for flag in ("--require-hashes", "--no-deps", "--disable-pip", "--strict", "--format json"):
        assert flag in audit
    for flag in ("--candidate-validation", "--hashed-install-report", "--resolver-report", "--pip-audit-report"):
        assert flag in coverage


def test_functional_probe_is_offline_and_never_certifies_or_authorizes() -> None:
    data = workflow()
    step = next(s for s in data["jobs"]["validate-candidate"]["steps"] if s["name"].startswith("Validate real Qlib"))
    for marker in ("--network none", "--read-only", "--user 10001:10001", "readonly",
                   'report.get("p08_allowed") is not False', 'report["security"]["exact_certified_graph"] is not False'):
        assert marker in step["run"]
    assert data["env"]["LIVE_ENABLED"] == "false"
    assert data["env"]["ORDER_SUBMISSION_ENABLED"] == "false"
    assert data["env"]["REAL_ORDER_SUBMISSION_ENABLED"] == "false"


def test_embedded_python_is_syntactically_valid() -> None:
    steps = workflow()["jobs"]["validate-candidate"]["steps"]
    scripts = [s["run"] for s in steps if "run" in s]
    snippets = [code for script in scripts for code in re.findall(r"python - <<'PY'\n(.*?)^PY$", script, re.M | re.S)]
    assert len(snippets) == 3
    for code in snippets:
        ast.parse(code)
