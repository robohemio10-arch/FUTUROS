from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_OUTPUT_DIR = Path(
    "data/reports/post_ocr_quant"
)


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
            "Reproduce the frozen official post-OCR "
            "economic baseline."
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
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
    )
    parser.add_argument(
        "--write-report",
        action="store_true",
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
        "schema_version": (
            "official_trades_master_baseline_v1"
        ),
        "engineering_status": "BLOCKED",
        "master_source_status": "BLOCKED",
        "official_baseline_status": "BLOCKED",
        "quant_edge_status": "NOT_EVALUATED_WQ1",
        "status": "blocked",
        "reason": reason,
        "details": details,
        "master_path": master_path,
        "read_only": True,
        "writes_master": False,
        "writes_runtime_trading_state": False,
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

    from smartcrypto.research.trades_master_official.baseline import (
        OfficialBaselineValidationError,
        build_official_trades_master_baseline,
    )
    from smartcrypto.research.trades_master_official.contracts import (
        CANONICAL_SOURCE_CONTRACT,
    )
    from smartcrypto.research.trades_master_official.loader import (
        OfficialMasterValidationError,
        load_official_trades_master,
    )
    from smartcrypto.research.trades_master_official.persistence import (
        persist_baseline_reports,
        report_content_sha256,
    )

    args = build_parser().parse_args(argv)

    source_contract = replace(
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
            contract=source_contract,
        )

        payload = (
            build_official_trades_master_baseline(
                master
            )
        )

        payload["status"] = "ok"
        payload["report_content_sha256"] = (
            report_content_sha256(payload)
        )

        if args.write_report:
            payload["report_paths"] = (
                persist_baseline_reports(
                    payload,
                    output_dir=args.output_dir,
                )
            )

    except OfficialMasterValidationError as exc:
        payload = _blocked_payload(
            reason=exc.code,
            details=exc.details,
            master_path=str(args.master),
        )

    except OfficialBaselineValidationError as exc:
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
        if payload.get("status") == "ok"
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
