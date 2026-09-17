from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = Path("data/reports/post_ocr_quant")


def _ensure_project_root() -> None:
    value = str(PROJECT_ROOT)

    if value not in sys.path:
        sys.path.insert(0, value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit frozen WQ4 regime/concentration evidence "
            "from the official post-OCR trades master."
        )
    )

    parser.add_argument("--master", required=True)
    parser.add_argument("--method-freeze-v2", required=True)
    parser.add_argument("--btc-existing", required=True)
    parser.add_argument("--eth-existing", required=True)
    parser.add_argument("--btc-backfill", required=True)
    parser.add_argument("--eth-backfill", required=True)

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


def _blocked(
    *,
    reason: str,
    details: dict[str, Any],
    master_path: str,
) -> dict[str, Any]:
    return {
        "schema_version": "official_trades_master_regime_concentration_v1",
        "status": "blocked",
        "engineering_status": "BLOCKED",
        "wq4_status": "BLOCKED",
        "decision": "MANTER_EM_RESEARCH",
        "reason": reason,
        "details": details,
        "master_path": master_path,
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "operational_authority": False,
        "changes_risk": False,
        "changes_model": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "writes_master": False,
        "writes_sqlite": False,
        "write_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    _ensure_project_root()

    from smartcrypto.research.trades_master_official.loader import (
        OfficialMasterValidationError,
        load_official_trades_master,
    )
    from smartcrypto.research.trades_master_official.regime_concentration import (
        OfficialRegimeConcentrationError,
        build_regime_concentration_report,
        persist_regime_concentration_reports,
        report_content_sha256,
    )

    args = build_parser().parse_args(argv)

    try:
        master = load_official_trades_master(args.master)

        payload = build_regime_concentration_report(
            master,
            project_root=PROJECT_ROOT,
            method_freeze_v2_path=args.method_freeze_v2,
            market_paths={
                "BTCUSDT_existing": args.btc_existing,
                "ETHUSDT_existing": args.eth_existing,
                "BTCUSDT_backfill": args.btc_backfill,
                "ETHUSDT_backfill": args.eth_backfill,
            },
        )

        payload["report_content_sha256"] = report_content_sha256(payload)
        payload["write_requested"] = bool(args.write_report)
        payload["write_performed"] = False

        if args.write_report:
            payload["report_paths"] = persist_regime_concentration_reports(
                payload,
                output_dir=args.output_dir,
            )
            payload["write_performed"] = True

    except OfficialMasterValidationError as exc:
        payload = _blocked(
            reason=exc.code,
            details=exc.details,
            master_path=str(args.master),
        )

    except OfficialRegimeConcentrationError as exc:
        payload = _blocked(
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
                allow_nan=False,
            )
        )
    else:
        for key, value in payload.items():
            print(f"{key}={value}")

    return 0 if payload.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
