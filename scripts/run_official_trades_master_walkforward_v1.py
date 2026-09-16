from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_OUTPUT_DIR = Path(
    "data/reports/post_ocr_quant"
)


def _ensure_project_root() -> None:
    value = str(
        PROJECT_ROOT
    )

    if value not in sys.path:
        sys.path.insert(
            0,
            value,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run frozen WQ2 temporal walk-forward "
            "on the official post-OCR master."
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
        "--output-dir",
        default=str(
            DEFAULT_OUTPUT_DIR
        ),
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


def _blocked(
    reason: str,
    details: dict[str, Any],
    master_path: str,
) -> dict[str, Any]:
    return {
        "schema_version": (
            "official_trades_master_walkforward_v1"
        ),
        "engineering_status": "BLOCKED",
        "wq2_status": "BLOCKED",
        "quant_edge_status": "NOT_EVALUATED",
        "status": "blocked",
        "reason": reason,
        "details": details,
        "master_path": master_path,
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "live": False,
        "canary": False,
        "sends_orders": False,
        "changes_risk": False,
        "changes_model": False,
        "operational_authority": False,
    }


def main(
    argv: list[str] | None = None,
) -> int:
    _ensure_project_root()

    from smartcrypto.research.trades_master_official.loader import (
        OfficialMasterValidationError,
        load_official_trades_master,
    )
    from smartcrypto.research.trades_master_official.walkforward import (
        OfficialWalkForwardValidationError,
        build_walkforward_report,
        persist_walkforward_reports,
        report_content_sha256,
    )

    args = build_parser().parse_args(
        argv
    )

    try:
        master = load_official_trades_master(
            args.master
        )

        payload = build_walkforward_report(
            master
        )

        payload["status"] = "ok"

        payload[
            "report_content_sha256"
        ] = report_content_sha256(
            payload
        )

        if args.write_report:
            payload[
                "report_paths"
            ] = persist_walkforward_reports(
                payload,
                output_dir=args.output_dir,
            )

    except OfficialMasterValidationError as exc:
        payload = _blocked(
            exc.code,
            exc.details,
            str(args.master),
        )

    except OfficialWalkForwardValidationError as exc:
        payload = _blocked(
            exc.code,
            exc.details,
            str(args.master),
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
        for (
            key,
            value,
        ) in payload.items():
            print(
                f"{key}={value}"
            )

    return (
        0
        if payload.get(
            "status"
        ) == "ok"
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
