from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import yaml

from scripts import docker_runtime_permissions_bootstrap as bootstrap


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.paper.yml"
RUNTIME_LOCK = ROOT / "requirements-runtime.lock"
BOOTSTRAP_SCRIPT = "scripts/docker_runtime_permissions_bootstrap.py"


def services() -> dict[str, dict[str, Any]]:
    payload = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    return payload["services"]


def command(service: dict[str, Any]) -> list[str]:
    return [str(item) for item in service["command"]]


def option_values(argv: list[str], option: str) -> list[str]:
    return [argv[index + 1] for index, value in enumerate(argv[:-1]) if value == option]


def assert_bootstrap_contract(service_name: str, expected_paths: set[str]) -> None:
    service = services()[service_name]
    argv = command(service)

    assert service["user"] == "0:0"
    assert argv[:4] == ["python", BOOTSTRAP_SCRIPT, "--service", service_name]
    assert set(option_values(argv, "--path")) == expected_paths
    assert len(option_values(argv, "--path")) == len(expected_paths)
    separator = argv.index("--")
    assert argv[separator + 1] == "python"
    assert argv[separator + 1] not in {"sh", "bash", "cmd", "powershell"}


def test_phase14_uses_ephemeral_root_bootstrap_with_exact_paths() -> None:
    assert_bootstrap_contract(
        bootstrap.PHASE14_SERVICE,
        {
            "/app/data/reports",
            "/app/data/trades",
            "/app/data/snapshots/freqtrade-paper",
        },
    )
    argv = command(services()[bootstrap.PHASE14_SERVICE])
    separator = argv.index("--")
    assert argv[separator + 1 :] == [
        "python",
        "scripts/run_phase14_runtime_feedback_sync.py",
        "--source-db",
        "/paper-db/tradesv3.paper.sqlite",
        "--interval-seconds",
        "120",
    ]


def test_autolearning_uses_ephemeral_root_bootstrap_with_exact_paths() -> None:
    assert_bootstrap_contract(
        bootstrap.AUTOLEARNING_SERVICE,
        {"/app/data/reports", "/app/data/feedback"},
    )
    argv = command(services()[bootstrap.AUTOLEARNING_SERVICE])
    separator = argv.index("--")
    assert argv[separator + 1 :] == [
        "python",
        "scripts/run_paper_autolearning_scheduler_v1.py",
        "--project-root",
        "/app",
        "--once",
        "--write-feedback",
        "--train-smoke",
        "--json",
    ]


def test_notifications_keep_existing_limited_bootstrap_without_execution() -> None:
    assert_bootstrap_contract(
        bootstrap.NOTIFICATION_SERVICE,
        {"/app/data/reports", "/app/data/runtime"},
    )
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert "smartcrypto.ops.trade_event_notifications" not in imported_modules


def test_operational_paper_db_and_dashboard_data_are_read_only() -> None:
    payload = services()
    for service_name in (bootstrap.PHASE14_SERVICE, bootstrap.NOTIFICATION_SERVICE):
        assert "freqtrade_paper_db:/paper-db:ro" in payload[service_name]["volumes"]
    assert "./data:/app/data:ro" in payload["smartcrypto-dashboard-paper"]["volumes"]


def test_runtime_services_preserve_paper_only_environment() -> None:
    payload = services()
    for service_name in (
        bootstrap.PHASE14_SERVICE,
        bootstrap.AUTOLEARNING_SERVICE,
        bootstrap.NOTIFICATION_SERVICE,
    ):
        environment = payload[service_name]["environment"]
        assert environment["SMARTCRYPTO_RUNTIME_MODE"] == "paper"
        assert environment["LIVE_ENABLED"] == "false"
        assert environment["ORDER_SUBMISSION_ENABLED"] == "false"
        assert environment["REAL_ORDER_SUBMISSION_ENABLED"] == "false"
        assert environment["SMARTCRYPTO_EXCHANGE_PRIVATE_ACCESS"] == "false"


def test_runtime_lock_contains_exactly_one_safe_gitpython_pin() -> None:
    requirements = [
        line.strip()
        for line in RUNTIME_LOCK.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    gitpython = [line for line in requirements if line.lower().startswith("gitpython==")]

    assert gitpython == ["GitPython==3.1.54"]
    assert all("==" in requirement for requirement in requirements)


def test_no_world_writable_mode_or_generic_data_authority() -> None:
    source = (ROOT / BOOTSTRAP_SCRIPT).read_text(encoding="utf-8")

    assert "0o777" not in source
    assert "chmod 777" not in source
    assert '"/app/data"' not in bootstrap.ALLOWED_RUNTIME_PATHS
    assert set(bootstrap.ALLOWED_RUNTIME_PATHS) == {
        "/app/data/reports",
        "/app/data/runtime",
        "/app/data/trades",
        "/app/data/feedback",
        "/app/data/features",
        "/app/data/predictions",
        "/app/data/snapshots/freqtrade-paper",
    }
