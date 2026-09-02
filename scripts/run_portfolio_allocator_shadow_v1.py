"""Run the deterministic W5 shadow Portfolio Allocator from JSON input."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from smartcrypto.research.portfolio_intelligence import (
    PortfolioAllocatorConfig,
    PortfolioAllocatorRequest,
    allocate_shadow_portfolio,
)
from smartcrypto.runtime.integrity_traceability_v2.atomic_writer import (
    AtomicWriteError,
    AtomicWritePolicy,
    atomic_write_json,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--project-root", default=".")
    result.add_argument("--config", default="config/research/portfolio_allocator.yaml")
    result.add_argument("--input-json", required=True)
    result.add_argument("--output-json", default=None)
    result.add_argument("--write-report", action="store_true")
    result.add_argument("--no-write", action="store_true")
    result.add_argument("--json", action="store_true")
    return result


def _mapping_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("input_json_must_be_mapping")
    return payload


def _mapping_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("config_must_be_mapping")
    return payload


def main() -> int:
    args = parser().parse_args()
    root = Path(args.project_root).resolve()
    config_path = (root / args.config).resolve() if not Path(args.config).is_absolute() else Path(args.config).resolve()
    input_path = Path(args.input_json).resolve()
    write_requested = bool(args.write_report and not args.no_write)
    try:
        config = PortfolioAllocatorConfig.model_validate(_mapping_yaml(config_path))
        request = PortfolioAllocatorRequest.model_validate(_mapping_json(input_path))
        allocation = allocate_shadow_portfolio(request, config)
    except (OSError, json.JSONDecodeError, yaml.YAMLError, ValidationError, ValueError) as exc:
        payload = {
            "status": "BLOCKED",
            "reason": f"allocator_validation_failed:{type(exc).__name__}",
            "write_requested": write_requested,
            "write_performed": False,
            "paper_only": True,
            "shadow_only": True,
            "research_only": True,
            "operational_authority": False,
            "sends_orders": False,
            "exchange_private_access": False,
            "changes_risk": False,
            "changes_model": False,
            "writes_active_signals": False,
        }
        print(json.dumps(payload, sort_keys=True))
        return 2

    output_paths: dict[str, str] = {}
    write_performed = False
    if write_requested:
        research_root = root / "data" / "research" / "portfolio_intelligence"
        policy = AtomicWritePolicy.restricted((research_root,), working_directory=root)
        target = (
            research_root / allocation.allocation_id / "portfolio_allocation.json"
            if args.output_json is None
            else Path(args.output_json)
        )
        try:
            result = atomic_write_json(
                target,
                allocation.model_dump(mode="json"),
                policy=policy,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
        except (AtomicWriteError, OSError, ValueError) as exc:
            payload = {
                "status": "BLOCKED",
                "reason": f"allocator_write_failed:{type(exc).__name__}",
                "write_requested": True,
                "write_performed": False,
                "paper_only": True,
                "shadow_only": True,
                "research_only": True,
                "operational_authority": False,
                "sends_orders": False,
                "exchange_private_access": False,
                "changes_risk": False,
                "changes_model": False,
                "writes_active_signals": False,
            }
            print(json.dumps(payload, sort_keys=True))
            return 2
        write_performed = result.write_performed
        output_paths["portfolio_allocation"] = (
            Path(result.target).resolve().relative_to(root).as_posix()
        )

    payload = {
        "status": allocation.status.value,
        "reason": allocation.reason,
        "allocation": allocation.model_dump(mode="json"),
        "write_requested": write_requested,
        "write_performed": write_performed,
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "operational_authority": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "changes_risk": False,
        "changes_model": False,
        "writes_active_signals": False,
        "riskmanager_final_authority": True,
        "output_paths": output_paths,
    }
    print(json.dumps(payload, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
