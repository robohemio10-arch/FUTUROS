"""Validate P0.4B runtime profile mappings without runtime integration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from smartcrypto.execution.decision_ledger_runtime_profile_v1 import (
    RuntimeDecisionProjectionV1,
    RuntimeTradeLinkProjectionV1,
    build_runtime_profile_schema,
    map_runtime_decision,
    map_runtime_trade_link,
    registry_payload,
    registry_sha256,
    validate_registry,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--fixture-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists():
        raise RuntimeError(f"temporary_file_exists:{temporary}")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    fixture_dir = Path(args.fixture_dir).resolve()
    output_dir = Path(args.output_dir).resolve()

    if not project_root.is_dir():
        raise RuntimeError("project_root_not_found")
    if not fixture_dir.is_dir():
        raise RuntimeError("fixture_dir_not_found")
    if "data" in {part.casefold() for part in output_dir.parts} and "runtime" in {
        part.casefold() for part in output_dir.parts
    }:
        raise RuntimeError("runtime_output_denied")

    validate_registry()
    decision_input = json.loads(
        (fixture_dir / "valid_runtime_decision_input.json").read_text(
            encoding="utf-8"
        )
    )
    trade_input = json.loads(
        (fixture_dir / "valid_runtime_trade_observation.json").read_text(
            encoding="utf-8"
        )
    )

    decision = map_runtime_decision(decision_input)
    trade_link = map_runtime_trade_link(decision, trade_input)

    TypeAdapter(RuntimeDecisionProjectionV1).validate_python(
        decision.model_dump(mode="python")
    )
    TypeAdapter(RuntimeTradeLinkProjectionV1).validate_python(
        trade_link.model_dump(mode="python")
    )

    output_dir.mkdir(parents=True, exist_ok=False)
    atomic_write_json(
        output_dir / "decision_projection.json",
        decision.model_dump(mode="json"),
    )
    atomic_write_json(
        output_dir / "trade_link_projection.json",
        trade_link.model_dump(mode="json"),
    )
    atomic_write_json(
        output_dir / "field_source_registry.json",
        registry_payload(),
    )
    atomic_write_json(
        output_dir / "runtime_profile_schema.json",
        build_runtime_profile_schema(),
    )

    result = {
        "schema_version": "p0_4b_runtime_profile_validator_v1",
        "status": "pass",
        "decision": (
            "P0_4B_RUNTIME_PROFILE_MAPPING_VALIDATED_"
            "NO_RUNTIME_INTEGRATION"
        ),
        "fixture_count": 2,
        "projection_count": 2,
        "required_target_field_count": 40,
        "field_source_registry_sha256": registry_sha256(),
        "decision_event_id": decision.target_payload.event_id,
        "decision_payload_sha256": decision.target_payload.payload_sha256,
        "trade_link_event_id": trade_link.target_payload.event_id,
        "trade_link_payload_sha256": trade_link.target_payload.payload_sha256,
        "authorization": {
            "runtime_integration_allowed": False,
            "branch_creation_allowed": False,
            "paper_restart_authorized": False,
        },
        "safety": {
            "writer_invoked": False,
            "runtime_modified": False,
            "sqlite_modified": False,
            "sends_orders": False,
            "exchange_private_access": False,
        },
    }
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(result["decision"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
