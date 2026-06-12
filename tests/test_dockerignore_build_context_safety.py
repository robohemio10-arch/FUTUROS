from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCKERIGNORE = ROOT / ".dockerignore"

REQUIRED_PATTERNS = {
    ".env",
    ".env.*",
    "!.env.example",
    "*.pem",
    "*.key",
    "*.crt",
    "*.p12",
    "*.pfx",
    "data/",
    "logs/",
    "reports/",
    "runtime/",
    "backups/",
    "*.sqlite",
    "*.sqlite3",
    "*.db",
    "*.parquet",
    "*.csv",
    "*.xlsx",
    "*.xls",
    "*.jsonl",
    "*.log",
    "*.zip",
    "**/__pycache__/",
    "*.pyc",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".coverage",
    "htmlcov/",
    ".venv/",
    "venv/",
    "env/",
    ".DS_Store",
    "Thumbs.db",
    ".vscode/",
    ".idea/",
    ".git/",
}


def dockerignore_entries() -> list[str]:
    return [
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_root_dockerignore_exists_with_critical_exclusions() -> None:
    assert DOCKERIGNORE.is_file()
    entries = set(dockerignore_entries())
    assert REQUIRED_PATTERNS <= entries


def test_env_example_exception_follows_env_exclusions() -> None:
    entries = dockerignore_entries()
    exception_index = entries.index("!.env.example")

    assert entries.index(".env") < exception_index
    assert entries.index(".env.*") < exception_index


def test_dockerignore_does_not_change_runtime_safety_contract() -> None:
    content = DOCKERIGNORE.read_text(encoding="utf-8").lower()

    assert "live_enabled=true" not in content
    assert "order_submission_enabled=true" not in content
    assert "real_order_submission_enabled=true" not in content
    assert "exchange_private_access=true" not in content
