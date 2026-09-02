"""Build a deterministic research-only Opportunity Book V2 from JSON input."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from smartcrypto.research.portfolio_intelligence import OpportunityBookRequest, build_opportunity_book
from smartcrypto.runtime.integrity_traceability_v2.atomic_writer import (
    AtomicWriteError,
    AtomicWritePolicy,
    atomic_write_json,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--project-root", default=".")
    result.add_argument("--input-json", required=True)
    result.add_argument("--output-json", default=None)
    result.add_argument("--min-replacement-delta-usdt", type=float, default=0.0)
    result.add_argument("--write-report", action="store_true")
    result.add_argument("--no-write", action="store_true")
    result.add_argument("--json", action="store_true")
    return result


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("input_json_must_be_mapping")
    return payload


def main() -> int:
    args = parser().parse_args()
    root = Path(args.project_root).resolve()
    input_path = Path(args.input_json).resolve()
    if not input_path.is_file() or input_path.is_symlink():
        raise SystemExit("input_json_missing_or_invalid")
    write_requested = bool(args.write_report and not args.no_write)
    try:
        request = OpportunityBookRequest.model_validate(_load_json(input_path))
        book = build_opportunity_book(
            request,
            min_replacement_delta_usdt=args.min_replacement_delta_usdt,
        )
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        payload = {
            "status": "BLOCKED",
            "reason": f"opportunity_book_validation_failed:{type(exc).__name__}",
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
            research_root / book.book_id / "opportunity_book.json"
            if args.output_json is None
            else Path(args.output_json)
        )
        try:
            result = atomic_write_json(
                target,
                book.model_dump(mode="json"),
                policy=policy,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
        except (AtomicWriteError, OSError, ValueError) as exc:
            payload = {
                "status": "BLOCKED",
                "reason": f"opportunity_book_write_failed:{type(exc).__name__}",
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
        output_paths["opportunity_book"] = (
            Path(result.target).resolve().relative_to(root).as_posix()
        )

    payload = {
        "status": book.status.value,
        "reason": book.reason,
        "book": book.model_dump(mode="json"),
        "write_requested": write_requested,
        "write_performed": write_performed,
        "output_paths": output_paths,
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
    print(json.dumps(payload, sort_keys=True, allow_nan=False))
    return 0 if book.status.value != "BLOCKED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
