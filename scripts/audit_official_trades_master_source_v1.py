from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _ensure_project_root() -> None:
    project_root = str(PROJECT_ROOT)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


def build_parser() -> argparse.ArgumentParser:
    _ensure_project_root()

    from smartcrypto.research.trades_master_official.contracts import (
        OFFICIAL_MASTER_SHA256,
    )

    parser = argparse.ArgumentParser(
        description=(
            "Audit the frozen official post-OCR trades master "
            "without modifying it."
        )
    )
    parser.add_argument(
        "--master",
        required=True,
        metavar="PATH",
        help=(
            "Path to the official post-OCR trades-master workbook. "
            "Required; no implicit filesystem default is permitted."
        ),
    )
    parser.add_argument(
        "--expected-sha256",
        default=OFFICIAL_MASTER_SHA256,
    )
    parser.add_argument(
        "--json",
        action="store_true",
    )
    return parser


def _blocked_payload(
    *,
    reason: str,
    details: dict[str, Any],
    master_path: str,
) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": reason,
        "details": details,
        "master_path": master_path,
        "read_only": True,
        "writes_master": False,
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "live": False,
        "canary": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "changes_risk": False,
        "operational_authority": False,
    }


def main(
    argv: list[str] | None = None,
) -> int:
    _ensure_project_root()

    from smartcrypto.research.trades_master_official.contracts import (
        CANONICAL_SOURCE_CONTRACT,
    )
    from smartcrypto.research.trades_master_official.loader import (
        OfficialMasterValidationError,
        load_official_trades_master,
    )

    args = build_parser().parse_args(argv)

    contract = replace(
        CANONICAL_SOURCE_CONTRACT,
        expected_sha256=(
            str(args.expected_sha256)
            .strip()
            .lower()
        ),
    )

    try:
        master = load_official_trades_master(
            args.master,
            contract=contract,
        )
        payload = master.audit.to_dict()
    except OfficialMasterValidationError as exc:
        payload = _blocked_payload(
            reason=exc.code,
            details=exc.details,
            master_path=str(args.master),
        )

    if args.json:
        print(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    else:
        for key, value in payload.items():
            print(f"{key}={value}")

    return (
        0
        if payload["status"] == "ok"
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
