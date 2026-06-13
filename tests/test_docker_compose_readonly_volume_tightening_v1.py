from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_docker_compose_readonly_volumes.py"
POLICY_PATH = Path("docs/DOCKER_COMPOSE_READONLY_VOLUME_TIGHTENING_V1.md")


def load_auditor() -> Any:
    spec = importlib.util.spec_from_file_location("docker_compose_readonly_volume_audit_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_text(root: Path, relative_path: str | Path, content: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def write_policy(root: Path, *exceptions: str) -> None:
    lines = [
        "policy_status: active",
        "paper_only: true",
        "shadow_only: true",
        "live_trading_enabled: false",
        "order_submission_enabled: false",
        "real_order_submission_enabled: false",
        "exchange_private_access: false",
        "sends_orders: false",
        "changes_risk: false",
    ]
    lines.extend(f"writable_exception: {item}" for item in exceptions)
    write_text(root, POLICY_PATH, "\n".join(lines) + "\n")


def write_compose(root: Path, volume: str, *, service: str = "app") -> None:
    write_text(
        root,
        "docker-compose.paper.yml",
        f"services:\n  {service}:\n    image: local\n    volumes:\n      - {volume}\n",
    )


def test_auditor_blocks_writable_code_config_or_scripts_mount() -> None:
    module = load_auditor()
    mounts = [
        {"compose_file": "docker-compose.paper.yml", "service": "app", "source": "./smartcrypto", "target": "/app/smartcrypto", "type": "bind", "read_only": False, "line": 5},
        {"compose_file": "docker-compose.paper.yml", "service": "app", "source": "./config", "target": "/app/config", "type": "bind", "read_only": False, "line": 6},
        {"compose_file": "docker-compose.paper.yml", "service": "app", "source": "./scripts", "target": "/app/scripts", "type": "bind", "read_only": False, "line": 7},
    ]

    findings = [module.classify_mount(mount, policy_documented=True, writable_exceptions=set()) for mount in mounts]

    assert all(item["classification"] == "writable_unjustified" for item in findings)
    assert all(item["severity"] == "high" for item in findings)


def test_auditor_classifies_data_logs_runtime_and_sqlite_as_writable_required(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_text(
        tmp_path,
        "docker-compose.paper.yml",
        "services:\n"
        "  app:\n"
        "    image: local\n"
        "    volumes:\n"
        "      - ./data:/app/data\n"
        "      - ./logs:/app/logs\n"
        "      - state:/freqtrade/user_data/db\n",
    )

    report = module.audit_project(tmp_path)

    assert report["status"] == "ok"
    assert report["writable_required_count"] == 3
    assert report["writable_unjustified_count"] == 0


def test_auditor_accepts_explicit_read_only_short_and_long_syntax(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_text(
        tmp_path,
        "docker-compose.paper.yml",
        "services:\n"
        "  app:\n"
        "    image: local\n"
        "    volumes:\n"
        "      - ./scripts:/app/scripts:ro\n"
        "      - type: bind\n"
        "        source: ./config\n"
        "        target: /app/config\n"
        "        read_only: true\n",
    )

    report = module.audit_project(tmp_path)

    assert report["status"] == "ok"
    assert report["read_only_count"] == 2
    assert all(item["classification"] == "read_only_ok" for item in report["volume_findings"])


def test_sensitive_writable_mount_is_critical_even_with_policy(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_compose(tmp_path, "./.env:/app/.env")

    report = module.audit_project(tmp_path)

    assert report["status"] == "blocked"
    assert report["volume_findings"][0]["severity"] == "critical"


def test_documented_broad_writable_exception_is_warning(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path, "docker-compose.live.example.yml|freqtrade-live|/freqtrade/user_data")
    write_text(
        tmp_path,
        "docker-compose.live.example.yml",
        "services:\n"
        "  freqtrade-live:\n"
        "    image: local\n"
        "    volumes:\n"
        "      - ./freqtrade/user_data:/freqtrade/user_data\n",
    )

    report = module.audit_project(tmp_path)
    finding = report["volume_findings"][0]

    assert report["status"] == "warning"
    assert finding["classification"] == "unknown_requires_review"
    assert finding["severity"] == "medium"
    assert finding["documented_exception"] is True


def test_auditor_output_is_deterministic(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_compose(tmp_path, "./data:/app/data")

    assert module.audit_project(tmp_path) == module.audit_project(tmp_path)


def test_auditor_preserves_safety_flags(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_compose(tmp_path, "./data:/app/data")

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


def test_auditor_is_static_without_docker_network_or_operational_dispatch() -> None:
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
    write_compose(tmp_path, "./data:/app/data")

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path), "--json"],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert payload["status"] == "ok"
    assert payload["writable_required_count"] == 1


def test_paper_compose_immutable_inputs_are_read_only() -> None:
    module = load_auditor()
    _services, mounts = module.parse_compose_mounts(ROOT / "docker-compose.paper.yml", "docker-compose.paper.yml")
    expected_targets = {"/app/config", "/app/scripts", "/app/smartcrypto"}

    for mount in mounts:
        if mount["target"] in expected_targets:
            assert mount["read_only"] is True, f"{mount['service']}:{mount['target']} must be read-only"


def test_real_repository_is_ok_or_documented_warning() -> None:
    module = load_auditor()

    report = module.audit_project(ROOT)

    assert report["status"] in {"ok", "warning"}
    assert report["status"] != "blocked"
    assert report["writable_unjustified_count"] == 0
    assert report["policy_documented"] is True
