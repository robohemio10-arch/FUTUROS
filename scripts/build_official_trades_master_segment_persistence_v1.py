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

DEFAULT_PARENT_WQ2_REPORT = (
    DEFAULT_OUTPUT_DIR
    / "OFFICIAL_TRADES_MASTER_WALKFORWARD_V1.json"
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
    _ensure_project_root()


    parser = argparse.ArgumentParser(
        description=(
            "Build frozen WQ3 segment persistence evidence "
            "from official post-OCR OOS trades."
        )
    )

    parser.add_argument(
        "--master",
        required=True,
    )

    parser.add_argument(
        "--parent-wq2-report",
        default=str(
            DEFAULT_PARENT_WQ2_REPORT
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
    *,
    reason: str,
    details: dict[str, Any],
    master_path: str,
) -> dict[str, Any]:
    return {
        "schema_version": (
            "official_trades_master_segment_persistence_v1"
        ),
        "engineering_status": "BLOCKED",
        "wq3_status": "BLOCKED",
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


def _read_json(
    path: str | Path,
) -> dict[str, Any]:
    target = Path(
        path
    )

    if not (
        target.exists()
        and target.is_file()
    ):
        raise ValueError(
            "parent_wq2_report_missing"
        )

    payload = json.loads(
        target.read_text(
            encoding="utf-8"
        )
    )

    if not isinstance(
        payload,
        dict,
    ):
        raise ValueError(
            "parent_wq2_report_not_object"
        )

    return payload


def main(
    argv: list[str] | None = None,
) -> int:
    _ensure_project_root()

    from smartcrypto.research.trades_master_official.loader import (
        OfficialMasterValidationError,
        load_official_trades_master,
    )
    from smartcrypto.research.trades_master_official.segment_persistence import (
        OfficialSegmentPersistenceError,
        build_segment_persistence_report,
        persist_segment_persistence_reports,
        report_content_sha256,
    )

    args = build_parser().parse_args(
        argv
    )

    try:
        parent = _read_json(
            args.parent_wq2_report
        )

        master = (
            load_official_trades_master(
                args.master
            )
        )

        payload = (
            build_segment_persistence_report(
                master,
                parent_wq2_report=parent,
            )
        )

        payload[
            "status"
        ] = "ok"

        payload[
            "report_content_sha256"
        ] = report_content_sha256(
            payload
        )

        if args.write_report:
            payload[
                "report_paths"
            ] = (
                persist_segment_persistence_reports(
                    payload,
                    output_dir=args.output_dir,
                )
            )

    except OfficialMasterValidationError as exc:
        payload = _blocked(
            reason=exc.code,
            details=exc.details,
            master_path=str(
                args.master
            ),
        )

    except OfficialSegmentPersistenceError as exc:
        payload = _blocked(
            reason=exc.code,
            details=exc.details,
            master_path=str(
                args.master
            ),
        )

    except ValueError as exc:
        payload = _blocked(
            reason=str(
                exc
            ),
            details={},
            master_path=str(
                args.master
            ),
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
        )
        == "ok"
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
