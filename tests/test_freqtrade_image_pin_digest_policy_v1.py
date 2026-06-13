from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_freqtrade_image_pin_digest_policy.py"
POLICY_RELATIVE_PATH = Path("docs/FREQTRADE_IMAGE_PIN_DIGEST_POLICY_V1.md")


def load_auditor() -> Any:
    spec = importlib.util.spec_from_file_location("freqtrade_image_pin_digest_policy_test", SCRIPT)
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
        POLICY_RELATIVE_PATH,
        "\n".join(
            (
                "policy_status: temporary_exception",
                "temporary_exception_allowed: true",
                "follow_up_branch: codex/freqtrade-image-digest-resolution-v1",
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


def write_compose(root: Path, image: str) -> None:
    write_text(root, "docker-compose.paper.yml", f"services:\n  freqtrade-paper:\n    image: {image}\n")


def test_auditor_detects_stable_without_digest_as_documented_warning(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_compose(tmp_path, "freqtradeorg/freqtrade:stable")

    report = module.audit_project(tmp_path)
    reference = report["freqtrade_image_references"][0]

    assert report["status"] == "warning"
    assert report["reason"] == "temporary_freqtrade_digest_exception_documented"
    assert report["stable_tag_count"] == 1
    assert report["unpinned_count"] == 1
    assert reference["severity"] == "medium"
    assert reference["mutable_tag"] is True


def test_auditor_blocks_latest_tag(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_compose(tmp_path, "freqtradeorg/freqtrade:latest")

    report = module.audit_project(tmp_path)

    assert report["status"] == "blocked"
    assert report["reason"] == "latest_freqtrade_tag_detected"
    assert report["latest_tag_count"] == 1
    assert report["freqtrade_image_references"][0]["severity"] == "high"


def test_auditor_accepts_valid_64_hex_digest(tmp_path: Path) -> None:
    module = load_auditor()
    digest = hashlib.sha256(b"registry-verified-unit-test-fixture").hexdigest()
    write_compose(tmp_path, f"freqtradeorg/freqtrade:2026.5@sha256:{digest}")

    report = module.audit_project(tmp_path)
    reference = report["freqtrade_image_references"][0]

    assert report["status"] == "ok"
    assert report["digest_pinned_count"] == 1
    assert report["unpinned_count"] == 0
    assert reference["digest_valid"] is True
    assert reference["severity"] == "ok"


def test_auditor_rejects_invalid_and_obvious_fake_digests(tmp_path: Path) -> None:
    module = load_auditor()
    write_text(
        tmp_path,
        "docker-compose.paper.yml",
        "services:\n"
        "  invalid:\n"
        "    image: freqtradeorg/freqtrade:2026.5@sha256:not-a-digest\n"
        "  fake:\n"
        f"    image: freqtradeorg/freqtrade:2026.5@sha256:{'0' * 64}\n",
    )

    report = module.audit_project(tmp_path)

    assert report["status"] == "blocked"
    assert report["reason"] == "invalid_or_placeholder_freqtrade_digest_detected"
    assert report["invalid_digest_count"] == 2
    assert all(item["severity"] == "high" for item in report["freqtrade_image_references"])


def test_auditor_detects_environment_variable_image_as_policy_required(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_compose(tmp_path, "${FREQTRADE_IMAGE}")

    report = module.audit_project(tmp_path)
    reference = report["freqtrade_image_references"][0]

    assert report["status"] == "warning"
    assert reference["tag"] == "variable"
    assert reference["variable_image"] is True
    assert reference["severity"] == "medium"


def test_mutable_image_without_policy_is_blocked(tmp_path: Path) -> None:
    module = load_auditor()
    write_compose(tmp_path, "freqtradeorg/freqtrade:stable")

    report = module.audit_project(tmp_path)

    assert report["status"] == "blocked"
    assert report["policy_documented"] is False
    assert report["temporary_exception_allowed"] is False


def test_auditor_output_is_deterministic(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_compose(tmp_path, "freqtradeorg/freqtrade:stable")

    assert module.audit_project(tmp_path) == module.audit_project(tmp_path)


def test_auditor_preserves_safety_flags(tmp_path: Path) -> None:
    module = load_auditor()
    write_policy(tmp_path)
    write_compose(tmp_path, "freqtradeorg/freqtrade:stable")

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


def test_auditor_has_no_docker_network_exchange_or_notification_execution() -> None:
    source = SCRIPT.read_text(encoding="utf-8").lower()

    assert "import docker" not in source
    assert "import requests" not in source
    assert "urllib.request" not in source
    assert "import ccxt" not in source
    assert "notificationdispatcher" not in source
    assert "shell=true" not in source


def test_cli_emits_deterministic_json(tmp_path: Path) -> None:
    write_policy(tmp_path)
    write_compose(tmp_path, "freqtradeorg/freqtrade:stable")

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


def test_repository_uses_controlled_temporary_exception() -> None:
    module = load_auditor()

    report = module.audit_project(ROOT)

    assert report["status"] in {"ok", "warning"}
    assert report["status"] != "blocked"
    if report["unpinned_count"]:
        assert report["status"] == "warning"
        assert report["policy_documented"] is True
        assert report["temporary_exception_allowed"] is True


def test_policy_documentation_does_not_claim_unpinned_stable_is_ok() -> None:
    text = (ROOT / POLICY_RELATIVE_PATH).read_text(encoding="utf-8").lower()

    assert "temporary_exception_allowed: true" in text
    assert "codex/freqtrade-image-digest-resolution-v1" in text
    assert "returns `warning`" in text
    assert "inventing or guessing a digest" in text
