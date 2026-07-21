"""Validate payload 4.2 fixtures using the isolated design-only writer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from smartcrypto.execution.decision_ledger_v4_2 import (
    DecisionLedgerWriter,
    parse_payload_record,
)

SCHEMA_VERSION = "p0_3b_payload_4_2_local_validator_v1"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    if temporary.exists():
        raise RuntimeError(f"temporary_report_exists:{temporary}")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--fixture-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    project_root = Path(args.project_root).resolve()
    fixture_dir = Path(args.fixture_dir).resolve()
    output_dir = Path(args.output_dir).resolve()

    expected_fixture_dir = project_root / "tests" / "fixtures" / "decision_ledger_v4_2"
    if fixture_dir != expected_fixture_dir.resolve():
        raise RuntimeError("fixture_dir_must_match_project_fixture_directory")

    output_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = output_dir / "decision_ledger_payload_v4_2.validation.jsonl"
    health_path = output_dir / "decision_ledger_payload_v4_2.health.json"
    report_path = output_dir / "p0_3b_payload_4_2_validation_report.json"

    for path in (ledger_path, health_path, report_path):
        if path.exists():
            raise RuntimeError(f"validation_output_already_exists:{path}")

    fixture_paths = sorted(fixture_dir.glob("*.json"))
    if not fixture_paths:
        raise RuntimeError("no_payload_fixtures_found")

    records = []
    for fixture_path in fixture_paths:
        records.append(
            parse_payload_record(fixture_path.read_text(encoding="utf-8"))
        )

    writer = DecisionLedgerWriter(
        ledger_path=ledger_path,
        health_path=health_path,
        allowed_root=output_dir,
        design_only=True,
    )
    receipts = [writer.append(record) for record in records]
    health = writer.read_health()

    persisted_lines = ledger_path.read_text(encoding="utf-8").splitlines()
    persisted_records = [parse_payload_record(line) for line in persisted_lines]

    decision_records = [item for item in persisted_records if item.record_type == "decision"]
    trade_links = [item for item in persisted_records if item.record_type == "trade_link"]
    if len(decision_records) != 1 or len(trade_links) != 1:
        raise RuntimeError("fixture_set_must_contain_one_decision_and_one_trade_link")
    if trade_links[0].parent_event_id != decision_records[0].event_id:
        raise RuntimeError("trade_link_parent_mismatch")
    if trade_links[0].decision_payload_sha256 != decision_records[0].payload_sha256:
        raise RuntimeError("trade_link_decision_hash_mismatch")

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "pass",
        "decision": "P0_3B_LOCAL_CONTRACT_VALIDATED_NO_RUNTIME_INTEGRATION",
        "fixture_count": len(fixture_paths),
        "ledger_record_count": len(persisted_records),
        "record_types": [record.record_type for record in persisted_records],
        "receipt_payload_hashes": [receipt.payload_sha256 for receipt in receipts],
        "health": health.model_dump(mode="json"),
        "authorization": {
            "local_design_only_allowed": True,
            "runtime_integration_allowed": False,
            "branch_creation_allowed": False,
            "paper_restart_authorized": False,
        },
        "safety": {
            "repository_modified": False,
            "runtime_modified": False,
            "container_started": False,
            "sqlite_modified": False,
            "sends_orders": False,
            "exchange_private_access": False,
        },
    }
    _atomic_write_json(report_path, report)

    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False))
    else:
        print(f"status={report['status']}")
        print(f"decision={report['decision']}")
        print(f"fixture_count={report['fixture_count']}")
        print(f"ledger_record_count={report['ledger_record_count']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "runtime_integration_allowed": False,
                    "branch_creation_allowed": False,
                    "paper_restart_authorized": False,
                    "sends_orders": False,
                    "exchange_private_access": False,
                },
                sort_keys=True,
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1)
