"""Validate P0.4C sandbox-only integration harness."""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smartcrypto.execution.decision_ledger_runtime_integration_v1 import (
    InMemoryProjectionSink,
    SandboxFileProjectionSink,
    SandboxIntegrationConfigV1,
    build_decision_index,
    inspect_legacy_strategy_writer,
    preview_after_risk_manager,
    preview_trade_link,
    validate_migration_mode,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--fixture-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--strategy-path")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    fixture_dir = Path(args.fixture_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    if output_dir.exists():
        raise RuntimeError("output_dir_already_exists")
    forbidden = {part.casefold() for part in output_dir.parts}
    if "data" in forbidden and "runtime" in forbidden:
        raise RuntimeError("runtime_output_denied")

    approved = json.loads((fixture_dir / "approved_signal.json").read_text(encoding="utf-8"))
    rejected = json.loads((fixture_dir / "rejected_signal.json").read_text(encoding="utf-8"))
    observation = json.loads((fixture_dir / "trade_observation.json").read_text(encoding="utf-8"))
    decision_time = datetime(2026, 7, 20, 18, 0, 2, tzinfo=timezone.utc)
    result = preview_after_risk_manager(
        approved_signals=[approved],
        rejected_signals=[rejected],
        decision_timestamp=decision_time,
        config=SandboxIntegrationConfigV1(mode="preview", enabled=True),
    )
    if result.status != "ok" or result.projected_decision_count != 2:
        raise RuntimeError("integration_preview_failed")

    memory_sink = InMemoryProjectionSink()
    for item in result.decision_projections:
        memory_sink.append(item)
    duplicate = memory_sink.append(result.decision_projections[0])

    index = build_decision_index(result.decision_projections)
    allowed_decision = next(
        item for item in result.decision_projections if item.target_payload.final_decision.value == "ALLOW"
    )
    trade_link = preview_trade_link(
        decision_index=index,
        request={
            "decision_event_id": allowed_decision.target_payload.event_id,
            "trade_observation": observation,
        },
    )
    if trade_link.status != "ok" or trade_link.projection is None:
        raise RuntimeError("trade_link_preview_failed")

    with tempfile.TemporaryDirectory(prefix="p04c_writer_") as temporary:
        root = Path(temporary)
        file_sink = SandboxFileProjectionSink(
            allowed_root=root,
            ledger_path=root / "ledger.jsonl",
            health_path=root / "health.json",
        )
        file_receipt = file_sink.append(allowed_decision)
        health = file_sink.health()

    strategy_path = (
        Path(args.strategy_path).resolve()
        if args.strategy_path
        else project_root / "freqtrade/user_data/strategies/SmartCryptoSignalStrategy.py"
    )
    migration_report = inspect_legacy_strategy_writer(strategy_path)
    migration_gate = validate_migration_mode(mode="legacy_only", report=migration_report)

    output_dir.mkdir(parents=True, exist_ok=False)
    write_json(output_dir / "integration_preview.json", result.model_dump(mode="json"))
    write_json(output_dir / "trade_link_preview.json", trade_link.model_dump(mode="json"))
    write_json(output_dir / "legacy_writer_guard.json", migration_report)

    summary = {
        "schema_version": "p0_4c_sandbox_integration_validator_v1",
        "status": "pass",
        "decision": "P0_4C_SANDBOX_INTEGRATION_VALIDATED_NO_RUNTIME_INTEGRATION",
        "decision_projection_count": result.projected_decision_count,
        "active_envelope_count": result.active_envelope_count,
        "trade_link_projection_count": 1,
        "memory_sink_unique_count": len(memory_sink.records()),
        "memory_duplicate_suppressed": duplicate.duplicate,
        "file_sink_append_count": 1,
        "file_sink_health_status": health["status"],
        "file_sink_receipt_event_id": file_receipt.event_id,
        "legacy_writer_guard": migration_gate,
        "writer_invoked_in_runtime": False,
        "runtime_integration_allowed": False,
        "branch_creation_allowed": False,
        "paper_restart_authorized": False,
        "sends_orders": False,
        "exchange_private_access": False,
    }
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(summary["decision"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
