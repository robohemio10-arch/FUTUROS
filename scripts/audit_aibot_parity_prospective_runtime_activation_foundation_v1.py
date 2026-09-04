#!/usr/bin/env python3
"""No-write audit for the AIBOT prospective runtime-activation foundation."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_PROJECT_ROOT_TEXT = str(_PROJECT_ROOT)
if _PROJECT_ROOT_TEXT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT_TEXT)

from smartcrypto.research.aibot_parity_paper_ab_prospective_collector import (  # noqa: E402
    build_paper_financial_config_fingerprint,
)
from smartcrypto.research.aibot_parity_paper_ab_prospective_collector.runtime_foundation import (  # noqa: E402
    RUNTIME_SAFETY_FLAGS,
    build_deployment_foundation_report,
    load_runtime_foundation_config,
)

SCHEMA_VERSION = "aibot_parity_prospective_runtime_activation_foundation_audit_v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--json", action="store_true")
    return parser


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def _function_return_contract(path: Path, function_name: str) -> bool:
    tree = _parse(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            rendered = ast.dump(node, include_attributes=False)
            return "report" in rendered and "status" in rendered
    return False


def build_audit(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    collector_path = (
        root
        / "smartcrypto/research/aibot_parity_paper_ab_prospective_collector/collector.py"
    )
    runtime_path = (
        root
        / "smartcrypto/research/aibot_parity_paper_ab_prospective_collector/runtime_foundation.py"
    )
    runner_path = root / "scripts/run_aibot_parity_paper_ab_prospective_collector_v1.py"
    cycle_path = root / "scripts/run_aibot_parity_prospective_runtime_cycle_v1.py"
    health_path = root / "scripts/check_aibot_parity_prospective_runtime_health_v1.py"

    config = load_runtime_foundation_config(project_root=root)
    fingerprint = build_paper_financial_config_fingerprint(
        project_root=root,
        paper_config_path=config.paper_config_path,
        strategy_path=config.strategy_path,
        strategy_name=config.strategy_name,
    )
    fingerprint_valid = (
        fingerprint.paper_financial_config_sha256
        == config.expected_financial_config_sha256
    )
    collector_source = collector_path.read_text(encoding="utf-8")
    runner_source = runner_path.read_text(encoding="utf-8")
    runtime_source = runtime_path.read_text(encoding="utf-8")

    checks: dict[str, bool] = {
        "COLLECTOR_PIT_FAIL_CLOSED": all(
            token in collector_source
            for token in (
                "AIBOT_SNAPSHOT_POINT_IN_TIME_NOT_VALID",
                "AIBOT_SNAPSHOT_BLOCKED",
            )
        ),
        "COLLECTOR_BLOCKER_PROPAGATION": "AB_SOAK_INTEGRITY:" in collector_source,
        "COLLECTOR_EXIT_CODE_CONTRACT": (
            'return 0 if report.get("status") == "ok" else 2' in runner_source
        ),
        "CANDIDATE_ID_INVARIANT": "candidate_id_reused_across_cycles" in collector_source,
        "FINANCIAL_CONFIG_FINGERPRINT_AVAILABLE": True,
        "FINANCIAL_CONFIG_FINGERPRINT_VALID": fingerprint_valid,
        "CAPTURED_AT_UTC_AVAILABLE": '"captured_at_utc"' in collector_source,
        "COLLECTOR_RUN_ID_AVAILABLE": '"collector_run_id"' in collector_source,
        "RECURRING_RUNNER_AVAILABLE": cycle_path.is_file(),
        "RUNNER_RESTART_SAFE": "_InterProcessFileLock" in runtime_source,
        "RUNNER_LOCK_SAFE": "cycle_lock.acquire()" in runtime_source,
        "RUNNER_IDEMPOTENT": (
            "write_observations_idempotent" in runtime_source
            and "write_assignments_idempotent" in runtime_source
        ),
        "HEARTBEAT_AVAILABLE": "HEARTBEAT_SCHEMA_VERSION" in runtime_source,
        "HEALTHCHECK_AVAILABLE": health_path.is_file(),
        "PAPER_BEHAVIOR_CHANGED": False,
        "TRAFFIC_SPLIT_PERFORMED": False,
        "TREATMENT_RUNTIME_ASSIGNMENT_PERFORMED": False,
        "WRITES_ACTIVE_SIGNALS": False,
        "SENDS_ORDERS": False,
        "PAPER_TREATMENT_RELEASE_ALLOWED": False,
        "PROSPECTIVE_COLLECTION_RUNNING_PROVEN": False,
        "COLLECTION_CLOCK_STARTED": False,
        "LIVE": False,
        "CANARY": False,
        "REAL_ORDER_SUBMISSION": False,
        "EXCHANGE_PRIVATE_ACCESS": False,
    }
    deployment = build_deployment_foundation_report(config)
    static_contracts = {
        "collector_compiles_to_ast": isinstance(_parse(collector_path), ast.Module),
        "runtime_foundation_compiles_to_ast": isinstance(_parse(runtime_path), ast.Module),
        "runner_main_has_status_return_contract": _function_return_contract(runner_path, "main"),
        "cycle_main_has_status_return_contract": _function_return_contract(cycle_path, "main"),
        "scheduler_registration_performed": False,
        "docker_service_added": False,
    }
    expected_true = (
        "COLLECTOR_PIT_FAIL_CLOSED",
        "COLLECTOR_BLOCKER_PROPAGATION",
        "COLLECTOR_EXIT_CODE_CONTRACT",
        "CANDIDATE_ID_INVARIANT",
        "FINANCIAL_CONFIG_FINGERPRINT_AVAILABLE",
        "FINANCIAL_CONFIG_FINGERPRINT_VALID",
        "CAPTURED_AT_UTC_AVAILABLE",
        "COLLECTOR_RUN_ID_AVAILABLE",
        "RECURRING_RUNNER_AVAILABLE",
        "RUNNER_RESTART_SAFE",
        "RUNNER_LOCK_SAFE",
        "RUNNER_IDEMPOTENT",
        "HEARTBEAT_AVAILABLE",
        "HEALTHCHECK_AVAILABLE",
    )
    expected_false = (
        "PAPER_BEHAVIOR_CHANGED",
        "TRAFFIC_SPLIT_PERFORMED",
        "TREATMENT_RUNTIME_ASSIGNMENT_PERFORMED",
        "WRITES_ACTIVE_SIGNALS",
        "SENDS_ORDERS",
        "PAPER_TREATMENT_RELEASE_ALLOWED",
        "PROSPECTIVE_COLLECTION_RUNNING_PROVEN",
        "COLLECTION_CLOCK_STARTED",
        "LIVE",
        "CANARY",
        "REAL_ORDER_SUBMISSION",
        "EXCHANGE_PRIVATE_ACCESS",
    )
    blockers = [key for key in expected_true if checks.get(key) is not True]
    blockers.extend(key for key in expected_false if checks.get(key) is not False)
    blockers.extend(
        key for key, value in static_contracts.items() if key.endswith("contract") and not value
    )
    blockers = list(dict.fromkeys(blockers))
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "blocked" if blockers else "ok",
        "reason": blockers[0] if blockers else "prospective_runtime_activation_foundation_ready",
        "blockers": blockers,
        "checks": checks,
        "static_contracts": static_contracts,
        "financial_config_fingerprint": fingerprint.to_dict(),
        "expected_financial_config_sha256": config.expected_financial_config_sha256,
        "deployment_foundation": deployment,
        "collection_clock_started": False,
        "prospective_collection_running_proven": False,
        "paper_treatment_release_allowed": False,
        "safety_flags": dict(RUNTIME_SAFETY_FLAGS),
        **RUNTIME_SAFETY_FLAGS,
    }


def main() -> int:
    args = _parser().parse_args()
    try:
        report = build_audit(args.project_root)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        report = {
            "schema_version": SCHEMA_VERSION,
            "status": "blocked",
            "reason": str(exc).splitlines()[0].strip()[:300] or type(exc).__name__,
            "blockers": [type(exc).__name__],
            "collection_clock_started": False,
            "prospective_collection_running_proven": False,
            "paper_treatment_release_allowed": False,
            "safety_flags": dict(RUNTIME_SAFETY_FLAGS),
            **RUNTIME_SAFETY_FLAGS,
        }
    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False, allow_nan=False))
    else:
        print(f"status={report.get('status')} reason={report.get('reason')}")
    return 0 if report.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
