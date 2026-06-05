from __future__ import annotations

import json
from pathlib import Path

import yaml

from smartcrypto.runtime.container_healthcheck import run_container_healthcheck


ROOT = Path(__file__).resolve().parents[1]
DOCKERFILES = (
    ROOT / "docker" / "smartcrypto" / "Dockerfile",
    ROOT / "docker" / "dashboard" / "Dockerfile",
    ROOT / "docker" / "qlib" / "Dockerfile",
)
FALSE_FLAGS = (
    "LIVE_ENABLED",
    "ORDER_SUBMISSION_ENABLED",
    "REAL_ORDER_SUBMISSION_ENABLED",
    "SMARTCRYPTO_EXCHANGE_PRIVATE_ACCESS",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def docker_user_instructions(text: str) -> list[str]:
    users: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("USER "):
            users.append(stripped.split(maxsplit=1)[1].strip())
    return users


def load_compose(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def environment_for(service: dict) -> dict[str, str]:
    env = service.get("environment") or {}
    if isinstance(env, list):
        pairs = [item.split("=", 1) for item in env if "=" in item]
        return {key: value for key, value in pairs}
    return dict(env)


def assert_flags_false(env: dict[str, str]) -> None:
    assert env["SMARTCRYPTO_RUNTIME_MODE"] == "paper"
    for flag in FALSE_FLAGS:
        assert env[flag] == "false"


def test_dockerfiles_define_non_root_user() -> None:
    for dockerfile in DOCKERFILES:
        text = read(dockerfile)
        users = docker_user_instructions(text)

        assert users, f"{dockerfile} must define a USER"
        assert "USER smartcrypto" in text
        assert "useradd --system" in text
        assert "SMARTCRYPTO_UID=10001" in text
        assert "SMARTCRYPTO_GID=10001" in text


def test_dockerfiles_do_not_end_as_root() -> None:
    for dockerfile in DOCKERFILES:
        final_user = docker_user_instructions(read(dockerfile))[-1].lower()

        assert final_user not in {"root", "0"}
        assert final_user == "smartcrypto"


def test_dockerfiles_keep_healthcheck_after_non_root_user() -> None:
    for dockerfile in DOCKERFILES:
        text = read(dockerfile)

        assert "HEALTHCHECK" in text
        assert "python -m smartcrypto.runtime.container_healthcheck --quiet" in text
        assert text.index("USER smartcrypto") < text.index("HEALTHCHECK")


def test_dockerfiles_prepare_runtime_dirs_before_user_switch() -> None:
    for dockerfile in DOCKERFILES:
        text = read(dockerfile)

        assert "mkdir -p /app/data" in text
        assert "/app/logs" in text
        assert "chown -R smartcrypto:smartcrypto /app" in text
        assert text.index("chown -R smartcrypto:smartcrypto /app") < text.index("USER smartcrypto")


def test_docker_compose_paper_keeps_live_and_orders_disabled() -> None:
    compose = load_compose("docker-compose.paper.yml")
    checked_services = {
        name: service
        for name, service in compose["services"].items()
        if name != "redis"
    }

    assert checked_services
    for service in checked_services.values():
        assert_flags_false(environment_for(service))


def test_docker_compose_live_example_remains_dry_run_and_order_blocked() -> None:
    compose = load_compose("docker-compose.live.example.yml")
    for service in compose["services"].values():
        assert_flags_false(environment_for(service))

    freqtrade_config = json.loads((ROOT / "freqtrade" / "user_data" / "config.live.example.json").read_text(encoding="utf-8"))
    assert freqtrade_config["dry_run"] is True
    assert freqtrade_config["exchange"]["key"] == ""
    assert freqtrade_config["exchange"]["secret"] == ""


def test_container_healthcheck_blocks_orders_and_private_exchange() -> None:
    report = run_container_healthcheck(
        required_paths=(),
        required_imports=(),
        env={
            "SMARTCRYPTO_RUNTIME_MODE": "paper",
            "LIVE_ENABLED": "false",
            "ORDER_SUBMISSION_ENABLED": "true",
            "REAL_ORDER_SUBMISSION_ENABLED": "true",
            "SMARTCRYPTO_EXCHANGE_PRIVATE_ACCESS": "true",
        },
    )

    assert report["status"] == "blocked"
    assert report["sends_orders"] is False
    assert "unsafe_safety_flag:order_submission_enabled" in report["blocking_findings"]
    assert "unsafe_safety_flag:real_order_submission_enabled" in report["blocking_findings"]
    assert "unsafe_safety_flag:exchange_private_access" in report["blocking_findings"]


def test_security_docs_document_permissions_and_paper_shadow_only() -> None:
    text = (ROOT / "docs" / "DOCKER_NON_ROOT_CONTAINER_SECURITY.md").read_text(encoding="utf-8").lower()

    assert "user smartcrypto" in text
    assert "healthcheck" in text
    assert "/app/data" in text
    assert "/app/logs" in text
    assert "paper/shadow only" in text
    assert "live_enabled=false" in text
    assert "order_submission_enabled=false" in text
    assert "real_order_submission_enabled=false" in text


def test_docker_security_changes_do_not_touch_runtime_state() -> None:
    tracked_runtime_patterns = ("data/", "models/", "reports/", ".parquet", ".sqlite", ".csv", ".xlsx", ".jsonl")
    changed_contract_files = {
        "docker/smartcrypto/Dockerfile",
        "docker/dashboard/Dockerfile",
        "docker/qlib/Dockerfile",
        "docker-compose.paper.yml",
        "docker-compose.live.example.yml",
        "tests/test_docker_non_root_container_security.py",
        "docs/DOCKER_NON_ROOT_CONTAINER_SECURITY.md",
    }

    for path in changed_contract_files:
        assert not any(pattern in path for pattern in tracked_runtime_patterns)
