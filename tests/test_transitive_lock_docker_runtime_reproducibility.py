from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SAFETY_FALSE_FLAGS = (
    "LIVE_ENABLED",
    "ORDER_SUBMISSION_ENABLED",
    "REAL_ORDER_SUBMISSION_ENABLED",
    "SMARTCRYPTO_EXCHANGE_PRIVATE_ACCESS",
)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def requirement_lines(path: str) -> list[str]:
    rows: list[str] = []
    buffer: list[str] = []
    for raw in read(path).splitlines():
        stripped = raw.strip()
        if not buffer and (not stripped or stripped.startswith("#")):
            continue
        buffer.append(stripped.rstrip("\\").strip())
        if stripped.endswith("\\"):
            continue
        row = " ".join(buffer)
        rows.append(row.split(" --hash=", 1)[0].strip())
        buffer = []
    return rows


def pip_audit_lines() -> list[str]:
    return [line.strip() for line in read("Makefile").splitlines() if "pip_audit" in line]


def test_transitive_lockfiles_exist_and_are_substantial() -> None:
    assert (ROOT / "requirements-runtime.lock").exists()
    assert (ROOT / "requirements-dev.lock").exists()
    assert (ROOT / "constraints.txt").exists()

    runtime = requirement_lines("requirements-runtime.lock")
    dev = requirement_lines("requirements-dev.lock")

    assert len(runtime) >= 30
    assert len(dev) >= 60
    assert "pyarrow==23.0.1" in {line.lower() for line in runtime}
    assert "pyarrow==23.0.1" in {line.lower() for line in dev}
    assert any(line.lower().startswith("pip-audit==") for line in dev)


def test_lockfiles_do_not_contain_placeholders_or_open_ranges() -> None:
    forbidden_tokens = ("todo", "placeholder", ">=", "<=", "~=", "!=")
    for path in ("requirements-runtime.lock", "requirements-dev.lock"):
        text = read(path).lower()
        assert not any(token in text for token in forbidden_tokens)
        assert "--hash=sha256:" in text
        for line in requirement_lines(path):
            assert re.match(r"^[A-Za-z0-9_.-]+==[A-Za-z0-9_.!+:-]+$", line), line


def test_qlib_direct_and_transitive_security_locks_are_hashed() -> None:
    direct = requirement_lines("requirements-qlib.lock")
    full = requirement_lines("requirements-qlib-security.lock")
    assert direct == ["pyqlib==0.9.7"]
    assert len(full) == 190
    assert "pyqlib==0.9.7" in {line.lower() for line in full}
    assert "mlflow==3.17.0" in {line.lower() for line in full}
    assert "gitpython==3.2.1" in {line.lower() for line in full}
    assert "certifi==2026.10.19" in {line.lower() for line in full}
    assert "pillow==13.0.0" in {line.lower() for line in full}
    assert "cryptography==50.0.0" in {line.lower() for line in full}
    assert "--hash=sha256:" in read("requirements-qlib.lock")
    assert "--hash=sha256:" in read("requirements-qlib-security.lock")


def test_dockerfiles_install_by_lock_or_constraints_before_local_package() -> None:
    smartcrypto = read("docker/smartcrypto/Dockerfile")
    dashboard = read("docker/dashboard/Dockerfile")
    qlib = read("docker/qlib/Dockerfile")

    for dockerfile in (smartcrypto, dashboard):
        assert "COPY pyproject.toml README.md requirements-runtime.lock ./" in dockerfile
        assert "python -m pip install --require-hashes -r requirements-runtime.lock" in dockerfile
        assert "python -m pip install --no-build-isolation --no-deps -e ." in dockerfile
        assert 'pip install -e ".' not in dockerfile
        assert "HEALTHCHECK" in dockerfile
        assert "USER smartcrypto" in dockerfile

    assert "COPY requirements-qlib-security.lock ./" in qlib
    assert "python -m pip install --require-hashes -r requirements-qlib-security.lock" in qlib
    assert "python -m pip install --no-build-isolation --no-deps -e ." in qlib
    assert "HEALTHCHECK" in qlib
    assert "USER smartcrypto" in qlib


def test_ci_installs_from_lock_and_preserves_paper_shadow_only() -> None:
    workflow = read(".github/workflows/ci.yml")
    payload = yaml.safe_load(workflow)
    env = payload["env"]

    assert "python -m pip install --require-hashes -r requirements-dev.lock" in workflow
    assert "python -m pip install --no-build-isolation --no-deps -e ." in workflow
    assert "docker build -f docker/smartcrypto/Dockerfile" in workflow
    for flag in SAFETY_FALSE_FLAGS:
        assert str(env[flag]).lower() == "false"
    assert str(env["SMARTCRYPTO_RUNTIME_MODE"]).lower() == "paper"
    assert "secrets." not in workflow


def test_makefile_pip_audit_covers_transitive_lock_without_no_deps() -> None:
    lines = pip_audit_lines()

    assert lines
    assert any("requirements-dev.lock" in line for line in lines)
    for line in lines:
        assert "--no-deps" not in line
        assert "--disable-pip" not in line


def test_qlib_workflows_resolve_before_auditing_every_hash_locked_package() -> None:
    for path in (".github/workflows/ci.yml", ".github/workflows/security-resolution.yml"):
        workflow = yaml.safe_load(read(path))
        commands: list[str] = []
        for job in workflow["jobs"].values():
            commands.extend(str(step["run"]) for step in job["steps"] if "run" in step)
        resolver_steps = [
            (index, command)
            for index, command in enumerate(commands)
            if "pip install --dry-run" in command and "requirements-qlib-security.lock" in command
        ]
        audit_steps = [
            (index, command)
            for index, command in enumerate(commands)
            if "pip_audit -r requirements-qlib-security.lock" in command
        ]
        assert len(resolver_steps) == len(audit_steps) == 1
        resolver_index, resolver = resolver_steps[0]
        audit_index, audit = audit_steps[0]
        assert resolver_index <= audit_index
        assert "--ignore-installed" in resolver
        assert "--no-build-isolation" in resolver
        assert "--require-hashes" in resolver
        assert "--report" in resolver
        assert "--no-deps" not in resolver
        assert "--require-hashes" in audit
        assert "--disable-pip" in audit
        assert "--strict" in audit
        assert "--no-deps" not in audit
        assert "--ignore-vuln" not in audit
        if resolver_index == audit_index:
            assert resolver.index("pip install --dry-run") < audit.index("pip_audit -r")


def test_documentation_explains_lock_docker_ci_and_audit_policy() -> None:
    text = read("docs/TRANSITIVE_LOCK_DOCKER_RUNTIME_REPRODUCIBILITY.md")

    assert "requirements-runtime.lock" in text
    assert "requirements-dev.lock" in text
    assert "python -m pip install --no-deps -e ." in text
    assert "python -m pip_audit -r requirements-dev.lock --progress-spinner off" in text
    assert "nao usa `--no-deps`" in text
    assert "LIVE_ENABLED=false" in text
    assert "ORDER_SUBMISSION_ENABLED=false" in text
    assert "REAL_ORDER_SUBMISSION_ENABLED=false" in text


def test_runtime_artifact_ignores_and_safety_flags_remain_institutional() -> None:
    gitignore = read(".gitignore")
    for token in (
        "data/",
        "reports/",
        "logs/",
        "models/",
        "*.sqlite",
        "*.parquet",
        "*.csv",
        "*.xlsx",
        "*.jsonl",
    ):
        assert token in gitignore

    for text in (read("docker-compose.paper.yml"), read("docker-compose.live.example.yml")):
        assert 'LIVE_ENABLED: "false"' in text
        assert 'ORDER_SUBMISSION_ENABLED: "false"' in text
        assert 'REAL_ORDER_SUBMISSION_ENABLED: "false"' in text
        assert 'SMARTCRYPTO_EXCHANGE_PRIVATE_ACCESS: "false"' in text
        assert 'LIVE_ENABLED: "true"' not in text
        assert 'ORDER_SUBMISSION_ENABLED: "true"' not in text
        assert 'REAL_ORDER_SUBMISSION_ENABLED: "true"' not in text
