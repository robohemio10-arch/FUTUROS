from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def make_targets() -> set[str]:
    text = read("Makefile")
    return {
        match.group("target")
        for match in re.finditer(r"^(?P<target>[A-Za-z0-9_.-]+):", text, flags=re.MULTILINE)
    }


def test_makefile_exists() -> None:
    assert (ROOT / "Makefile").exists()


def test_makefile_contains_minimum_targets() -> None:
    required = {
        "install",
        "test",
        "test-fast",
        "compile",
        "lint",
        "typecheck",
        "security",
        "audit",
        "paper-check",
        "clean-cache",
    }

    assert required <= make_targets()


def test_makefile_commands_preserve_paper_shadow_only() -> None:
    text = read("Makefile")

    assert "compileall scripts smartcrypto tests" in text
    assert "pytest -q" in text
    assert "LIVE_ENABLED" in text
    assert "ORDER_SUBMISSION_ENABLED" in text
    assert "REAL_ORDER_SUBMISSION_ENABLED" in text
    forbidden = ["create_order", "fetch_balance", "private_get", "REAL_ORDER_SUBMISSION_ENABLED=true", "LIVE_ENABLED=true"]
    assert not any(token in text for token in forbidden)


def test_ci_workflow_exists() -> None:
    assert (ROOT / ".github" / "workflows" / "ci.yml").exists()


def test_ci_does_not_reference_dangerous_secrets() -> None:
    text = read(".github/workflows/ci.yml")

    forbidden = [
        "secrets.",
        "BINANCE_API_KEY",
        "BINANCE_SECRET",
        "EXCHANGE_SECRET",
        "PRIVATE_KEY",
        "LIVE_ENABLED: \"true\"",
        "ORDER_SUBMISSION_ENABLED: \"true\"",
        "REAL_ORDER_SUBMISSION_ENABLED: \"true\"",
    ]
    assert not any(token in text for token in forbidden)


def test_ci_keeps_live_and_order_submission_disabled() -> None:
    text = read(".github/workflows/ci.yml")

    assert 'SMARTCRYPTO_RUNTIME_MODE: paper' in text
    assert 'LIVE_ENABLED: "false"' in text
    assert 'ORDER_SUBMISSION_ENABLED: "false"' in text
    assert 'REAL_ORDER_SUBMISSION_ENABLED: "false"' in text
    assert "python -m compileall scripts smartcrypto tests" in text
    assert "python -m pytest -q" in text
    assert '"git", "ls-files"' in text or "git ls-files" in text


def test_pyproject_exposes_test_and_dev_extras_with_pyarrow() -> None:
    payload = tomllib.loads(read("pyproject.toml"))
    optional = payload["project"]["optional-dependencies"]
    for extra in ("test", "dev"):
        deps = "\n".join(optional[extra]).lower()
        assert "pytest" in deps
        assert "pandas" in deps
        assert "numpy" in deps
        assert "pyarrow" in deps
        assert "sqlalchemy" in deps
        assert "scikit-learn" in deps
        assert "streamlit" in deps


def test_lockfile_or_lock_strategy_exists() -> None:
    assert (ROOT / "requirements-dev.lock").exists() or (ROOT / "uv.lock").exists()
    text = read("requirements-dev.lock")
    assert "pyarrow==" in text
    assert "pytest==" in text


def test_gitignore_protects_runtime_artifacts() -> None:
    text = read(".gitignore")
    required = ["data/", "logs/", "reports/", "models/", "*.parquet", "*.sqlite", "*.csv", "*.xlsx", "*.jsonl"]
    for item in required:
        assert item in text


def test_documentation_explains_reproducible_commands_and_safety() -> None:
    text = read("docs/REPRODUCIBLE_DEV_ENVIRONMENT_CI_MAKEFILE.md")

    assert "make install" in text
    assert "python -m pip install -e \".[dev,test]\"" in text
    assert "pyarrow" in text.lower()
    assert "LIVE_ENABLED=false" in text
    assert "ORDER_SUBMISSION_ENABLED=false" in text
    assert "REAL_ORDER_SUBMISSION_ENABLED=false" in text
    assert "não envia ordens" in text.lower() or "nao envia ordens" in text.lower()
