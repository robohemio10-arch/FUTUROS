from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from smartcrypto.ops.versioned_file_discovery import discover_versioned_files


ROOT = Path(__file__).resolve().parents[1]


def load_script_module(name: str, relative_path: str) -> Any:
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def standalone_project(tmp_path: Path) -> Path:
    project = tmp_path / "standalone_project"
    write_text(project / "scripts" / "example.py", "print('paper only')\n")
    write_text(project / "smartcrypto" / "__init__.py", "")
    write_text(project / "smartcrypto" / "ops" / "__init__.py", "")
    write_text(project / "smartcrypto" / "ops" / "example.py", "PAPER_ONLY = True\n")
    write_text(project / "tests" / "test_example.py", "def test_ok():\n    assert True\n")
    write_text(project / "docs" / "example.md", "# Example\n")
    write_text(project / ".github" / "workflows" / "ci.yml", "name: ci\n")
    write_text(project / "docker" / "smartcrypto" / "Dockerfile", "FROM python:3.12-slim\n")
    write_text(project / "Makefile", "test:\n\tpython -m pytest -q\n")
    write_text(project / "constraints.txt", "pyqlib==0.9.7\n")
    write_text(project / "requirements-dev.lock", "pytest==9.0.3\n")
    write_text(project / "requirements-runtime.lock", "pandas==2.2.3\n")
    write_text(project / ".env.example", "LIVE_ENABLED=false\n")
    write_text(project / "data" / "reports" / "runtime_secret.txt", "api_key = 'A' * 40\n")
    write_text(project / "reports" / "runtime_report.md", "token = 'B' * 40\n")
    write_text(project / "logs" / "runtime.log", "secret = 'C' * 40\n")
    write_text(project / "evidence" / "runtime.txt", "secret = 'D' * 40\n")
    write_text(project / "data" / "features" / "features.parquet", "not really parquet\n")
    write_text(project / "archive.zip", "zip bytes placeholder\n")
    write_text(project / ".env", "BINANCE_SECRET=do-not-scan-runtime\n")
    return project


def run_script(relative_path: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / relative_path), *args],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )


def test_manifest_check_works_in_standalone_copy_without_git(tmp_path: Path) -> None:
    project = standalone_project(tmp_path)

    generate = run_script(
        "scripts/generate_project_manifest.py",
        "--project-root",
        str(project),
        "--output",
        "PROJECT_MANIFEST_CLEAN.json",
    )
    assert generate.returncode == 0, generate.stderr

    check = run_script("scripts/generate_project_manifest.py", "--project-root", str(project), "--check")
    payload = json.loads(check.stdout)

    assert check.returncode == 0
    assert payload["status"] == "ok"
    assert payload["reason"] == "manifest_current"


def test_secret_scan_works_in_standalone_copy_without_git(tmp_path: Path) -> None:
    project = standalone_project(tmp_path)
    run_script("scripts/generate_project_manifest.py", "--project-root", str(project), "--output", "PROJECT_MANIFEST_CLEAN.json")

    completed = run_script("scripts/scan_versioned_secrets.py", "--project-root", str(project), "--json")
    payload = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert payload["status"] == "ok"
    assert payload["file_discovery_mode"] == "manifest_baseline"
    assert payload["paper_only"] is True
    assert payload["shadow_only"] is True
    assert payload["live_trading_enabled"] is False
    assert payload["order_submission_enabled"] is False
    assert payload["real_order_submission_enabled"] is False
    assert payload["exchange_private_access"] is False
    assert payload["sends_orders"] is False


def test_standalone_fallback_ignores_runtime_paths_and_artifact_suffixes(tmp_path: Path) -> None:
    project = standalone_project(tmp_path)
    discovered = discover_versioned_files(project)

    assert discovered.mode == "filesystem"
    assert all(not path.startswith(("data/", "reports/", "logs/", "evidence/")) for path in discovered.files)
    assert all(not path.endswith((".sqlite", ".db", ".parquet", ".csv", ".xlsx", ".jsonl", ".log", ".zip")) for path in discovered.files)
    assert ".env" not in discovered.files


def test_standalone_fallback_includes_versionable_project_sources(tmp_path: Path) -> None:
    project = standalone_project(tmp_path)
    files = set(discover_versioned_files(project).files)

    expected = {
        ".env.example",
        ".github/workflows/ci.yml",
        "Makefile",
        "constraints.txt",
        "docker/smartcrypto/Dockerfile",
        "docs/example.md",
        "requirements-dev.lock",
        "requirements-runtime.lock",
        "scripts/example.py",
        "smartcrypto/__init__.py",
        "smartcrypto/ops/example.py",
        "tests/test_example.py",
    }
    assert expected <= files


def test_standalone_manifest_output_is_deterministic(tmp_path: Path) -> None:
    project = standalone_project(tmp_path)
    module = load_script_module("generate_project_manifest_zip_test", "scripts/generate_project_manifest.py")

    assert module.build_manifest(project) == module.build_manifest(project)


def test_git_repository_still_prefers_git_ls_files() -> None:
    discovered = discover_versioned_files(ROOT)

    assert discovered.mode == "git"
    assert discovered.source == "git ls-files"


def test_no_runtime_artifact_is_treated_as_versioned_in_standalone(tmp_path: Path) -> None:
    project = standalone_project(tmp_path)
    files = discover_versioned_files(project).files
    forbidden_fragments = ("runtime_secret", "runtime_report", "runtime.log", "features.parquet", "archive.zip")

    assert not any(any(fragment in path for fragment in forbidden_fragments) for path in files)
