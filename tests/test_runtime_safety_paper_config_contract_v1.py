from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/runtime_safety.paper.yml"
VALIDATOR = ROOT / "scripts/validate_runtime_safety_config.py"
RUNTIME_REPORT = ROOT / "data/runtime/runtime_safety_audit_config.json"

REQUIRED_FALSE_FLAGS = (
    "live_trading_enabled",
    "canary_release_allowed",
    "live_release_allowed",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "dashboard_can_change_risk",
    "dashboard_can_enable_live",
    "dashboard_can_promote_model",
    "ai_can_increase_risk",
    "ai_can_change_leverage",
    "ai_can_change_stake",
)

REQUIRED_POSITIVE_LIMITS = (
    "max_drawdown_pct",
    "max_daily_loss_pct",
    "max_weekly_loss_pct",
    "max_consecutive_losses",
    "max_spread_bps",
    "max_slippage_bps",
    "max_latency_ms",
    "max_data_age_seconds",
    "stale_prediction_max_age_seconds",
)

ALLOWED_CHANGED_PATHS = {
    ".github/workflows/ci.yml",
    ".gitignore",
    "PROJECT_MANIFEST_CLEAN.json",
    "config/runtime_safety.paper.yml",
    "docs/RUNTIME_SAFETY_PAPER_CONFIG_CONTRACT_V1.md",
    "tests/test_runtime_safety_paper_config_contract_v1.py",
}


def load_config() -> dict[str, Any]:
    payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def runtime_report_state() -> tuple[bool, bytes | None]:
    return (
        RUNTIME_REPORT.exists(),
        RUNTIME_REPORT.read_bytes() if RUNTIME_REPORT.exists() else None,
    )


def run_validator(report_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    completed = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR),
            "--config",
            str(CONFIG),
            "--environment",
            "paper",
            "--report",
            str(report_path),
            "--strict",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout), json.loads(
        report_path.read_text(encoding="utf-8")
    )


def test_canonical_config_exists_and_parses() -> None:
    assert CONFIG.is_file()
    config = load_config()
    assert config["schema_version"] == "runtime_safety_config_v1"
    assert config["config_version"] == "runtime_safety_paper_v1"
    assert config["runtime_mode"] == "paper"


def test_required_paper_shadow_guards_are_enabled() -> None:
    config = load_config()
    assert config["dry_run"] is True
    assert config["paper_only"] is True
    assert config["shadow_only"] is True
    assert config["kill_switch_enabled"] is True


def test_live_order_private_exchange_and_authority_flags_are_false() -> None:
    config = load_config()
    assert all(config[flag] is False for flag in REQUIRED_FALSE_FLAGS)


def test_required_limits_exist_and_are_positive() -> None:
    config = load_config()
    assert all(
        isinstance(config[key], int | float)
        and not isinstance(config[key], bool)
        and config[key] > 0
        for key in REQUIRED_POSITIVE_LIMITS
    )


def test_validator_strict_accepts_canonical_contract(tmp_path: Path) -> None:
    stdout_payload, report = run_validator(tmp_path / "runtime_safety_audit.json")
    assert stdout_payload == report
    assert report["status"] == "ok"
    assert report["reason"] == "runtime_safety_config_ok"
    assert report["schema_version"] == "runtime_safety_config_v1"
    assert report["config_version"] == "runtime_safety_paper_v1"
    assert report["runtime_mode"] == "paper"
    assert report["blocking_findings"] == []
    assert report["missing_required_keys"] == []
    assert report["unsafe_flags"] == []
    assert report["warnings"] == []
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["kill_switch_enabled"] is True
    assert report["live_trading_enabled"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False
    assert report["exchange_private_access"] is False
    assert report["sends_orders"] is False


def test_validator_does_not_create_or_change_runtime_report(tmp_path: Path) -> None:
    before = runtime_report_state()
    run_validator(tmp_path / "runtime_safety_audit.json")
    assert runtime_report_state() == before
    tracked = subprocess.run(
        ["git", "ls-files", "--", "data/runtime/runtime_safety_audit_config.json"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert tracked.stdout.strip() == ""


def test_worktree_changes_remain_inside_branch_allowlist() -> None:
    completed = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    changed = {
        line[3:].strip().replace("\\", "/")
        for line in completed.stdout.splitlines()
        if line.strip()
    }
    assert changed <= ALLOWED_CHANGED_PATHS
