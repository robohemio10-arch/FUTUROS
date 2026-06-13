from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_lockfile_hash_integrity.py"
POLICY_PATH = Path("docs/LOCKFILE_HASH_INTEGRITY_HARDENING_V1.md")


def load_auditor() -> Any:
    spec = importlib.util.spec_from_file_location("lockfile_hash_integrity_audit_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_text(root: Path, relative_path: str | Path, content: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def write_policy(root: Path) -> None:
    write_text(
        root,
        POLICY_PATH,
        "\n".join(
            (
                "policy_status: temporary_exception",
                "temporary_exception_allowed: true",
                "follow_up_branch: codex/lockfile-full-hash-resolution-v1",
                "paper_only: true",
                "shadow_only: true",
                "live_trading_enabled: false",
                "order_submission_enabled: false",
                "real_order_submission_enabled: false",
                "exchange_private_access: false",
                "sends_orders: false",
                "changes_risk: false",
            )
        )
        + "\n",
    )


def test_auditor_detects_requirement_with_valid_hash(tmp_path: Path) -> None:
    module = load_auditor()
    digest = hashlib.sha256(b"controlled-resolver-fixture").hexdigest()
    write_policy(tmp_path)
    write_text(tmp_path, "requirements-runtime.lock", f"pandas==2.2.3 --hash=sha256:{digest}\n")

    report = module.audit_project(tmp_path)

    assert report["status"] == "ok"
    assert report["hash_coverage"]["hashed_requirement_count"] == 1
    assert report["hash_coverage"]["invalid_hash_count"] == 0


def test_auditor_blocks_invalid_hash(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_text(tmp_path, "requirements-runtime.lock", "pandas==2.2.3 --hash=sha256:not-valid\n")

    report = module.audit_project(tmp_path)

    assert report["status"] == "blocked"
    assert report["high_count"] == 1
    assert report["hash_coverage"]["invalid_hash_count"] == 1


def test_auditor_blocks_placeholder_or_fake_hash(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_text(tmp_path, "requirements-runtime.lock", f"pandas==2.2.3 --hash=sha256:{'0' * 64}\n")

    report = module.audit_project(tmp_path)

    assert report["status"] == "blocked"
    assert report["critical_count"] == 1
    assert report["hash_coverage"]["placeholder_hash_count"] == 1


def test_unhashed_pinned_requirement_is_documented_warning(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_text(tmp_path, "requirements-runtime.lock", "pandas==2.2.3\n")

    report = module.audit_project(tmp_path)

    assert report["status"] == "warning"
    assert report["high_count"] == 0
    assert report["medium_count"] == 1
    assert report["hash_coverage"]["unhashed_requirement_count"] == 1
    assert report["findings"][0]["pattern"] == "pinned_without_hash"


def test_unpinned_entry_in_lockfile_is_blocked(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_text(tmp_path, "requirements-runtime.lock", "pandas>=2.2\n")

    report = module.audit_project(tmp_path)

    assert report["status"] == "blocked"
    assert any(item["pattern"] == "unpinned_dependency" and item["severity"] == "high" for item in report["findings"])


def test_unhashed_remote_dependency_is_critical(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_text(tmp_path, "requirements-runtime.lock", "package @ https://packages.example.invalid/package.whl\n")

    report = module.audit_project(tmp_path)

    assert report["status"] == "blocked"
    assert any(item["pattern"] == "unhashed_remote_dependency" for item in report["findings"])


def test_dockerfile_upgrade_is_visible_under_temporary_policy(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_text(tmp_path, "requirements-runtime.lock", "pandas==2.2.3\n")
    write_text(
        tmp_path,
        "docker/smartcrypto/Dockerfile",
        "FROM python:3.12-slim\n"
        "RUN python -m pip install --upgrade pip setuptools wheel \\\n"
        "    && python -m pip install -r requirements-runtime.lock\n",
    )

    report = module.audit_project(tmp_path)

    assert report["status"] == "warning"
    assert any(
        item["pattern"] == "pip_install_upgrade_in_runtime_build" and item["severity"] == "medium"
        for item in report["docker_install_findings"]
    )


def test_dockerfile_install_without_lock_is_blocked_without_policy(tmp_path: Path) -> None:
    module = load_auditor()
    write_text(tmp_path, "requirements.txt", "pandas==2.2.3\n")
    write_text(tmp_path, "Dockerfile", "FROM python:3.12-slim\nRUN pip install -r requirements.txt\n")

    report = module.audit_project(tmp_path)

    assert report["status"] == "blocked"
    assert any(item["pattern"] == "docker_install_without_lock_or_constraint" for item in report["findings"])


def test_auditor_output_is_deterministic(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_text(tmp_path, "requirements-runtime.lock", "pandas==2.2.3\n")

    assert module.audit_project(tmp_path) == module.audit_project(tmp_path)


def test_auditor_preserves_safety_flags(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_text(tmp_path, "requirements-runtime.lock", "pandas==2.2.3\n")

    report = module.audit_project(tmp_path)

    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["sends_orders"] is False
    assert report["changes_risk"] is False
    assert report["exchange_private_access"] is False
    assert report["live_trading_enabled"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False
    assert report["canary_release_allowed"] is False
    assert report["live_release_allowed"] is False


def test_auditor_is_static_and_does_not_install_or_access_external_services() -> None:
    source = SCRIPT.read_text(encoding="utf-8").lower()

    assert "import docker" not in source
    assert "import requests" not in source
    assert "urllib.request" not in source
    assert "import ccxt" not in source
    assert "notificationdispatcher" not in source
    assert "subprocess.run" not in source
    assert "shell=true" not in source


def test_cli_returns_controlled_json(tmp_path: Path) -> None:
    write_policy(tmp_path)
    write_text(tmp_path, "requirements-runtime.lock", "pandas==2.2.3\n")

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path), "--json"],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert payload["status"] == "warning"
    assert payload["policy_documented"] is True


def test_real_repository_is_not_blocked_by_documented_current_policy() -> None:
    module = load_auditor()

    report = module.audit_project(ROOT)

    assert report["status"] in {"ok", "warning"}
    assert report["status"] != "blocked"
    assert report["critical_count"] == 0
    assert report["high_count"] == 0
    assert report["policy_documented"] is True
    assert report["temporary_exception_allowed"] is True
