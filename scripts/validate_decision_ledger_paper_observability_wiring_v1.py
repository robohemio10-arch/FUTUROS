"""Validate the paper observability profile without running producers or writers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from smartcrypto.execution.decision_ledger_paper_observability_wiring_v1 import (
    DEFAULT_CONFIG_PATH,
    load_observability_config,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate disabled Decision Ledger paper observability wiring."
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def build_report(*, project_root: Path, config_path: Path) -> dict[str, Any]:
    root = project_root.expanduser().resolve(strict=False)
    resolved = config_path if config_path.is_absolute() else root / config_path
    config = load_observability_config(resolved)
    disabled_contract_valid = (
        config.enabled is False
        and config.writer_enabled is False
        and config.trade_link_enabled is False
        and config.writer_profile.enabled is False
    )
    safety = config.safety_flags.model_dump(mode="json")
    return {
        "schema_version": "decision_ledger_paper_observability_validator_v1",
        "status": "ok" if disabled_contract_valid else "blocked",
        "reason": (
            "paper_observability_profile_valid_disabled_by_default"
            if disabled_contract_valid
            else "paper_observability_profile_not_disabled"
        ),
        "decision": "KEEP_DISABLED_NO_RUNTIME_EXECUTION",
        "config_path": str(resolved),
        "enabled": config.enabled,
        "writer_enabled": config.writer_enabled,
        "trade_link_enabled": config.trade_link_enabled,
        "writer_invoked": False,
        "writes_runtime": False,
        "paper_behavior_changed": False,
        "runtime_execution_performed": False,
        "safety_flags": safety,
        **safety,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = build_report(
            project_root=Path(args.project_root),
            config_path=Path(args.config),
        )
    except (OSError, ValueError, ValidationError) as exc:
        report = {
            "schema_version": "decision_ledger_paper_observability_validator_v1",
            "status": "blocked",
            "reason": f"profile_validation_failed:{type(exc).__name__}",
            "decision": "KEEP_DISABLED_NO_RUNTIME_EXECUTION",
            "enabled": False,
            "writer_enabled": False,
            "trade_link_enabled": False,
            "writer_invoked": False,
            "writes_runtime": False,
            "paper_behavior_changed": False,
            "runtime_execution_performed": False,
            "sends_orders": False,
            "exchange_private_access": False,
            "changes_risk": False,
        }
    print(
        json.dumps(report, ensure_ascii=False, indent=2 if args.json else None, sort_keys=True)
        if args.json
        else f"{report['status']}:{report['reason']}"
    )
    return 1 if report["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
