"""Build the official post-OCR PIT Qlib research dataset.

No-write by default. No training, promotion, order submission, private exchange
access, RiskManager mutation, Freqtrade mutation, or official-master writes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.trades_master_official.qlib_dataset import (  # noqa: E402
    QlibDatasetValidationError,
    build_official_trades_master_qlib_dataset,
    persist_qlib_dataset_artifacts,
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description=__doc__,
    )

    value.add_argument("--master", required=True)
    value.add_argument("--existing-btc", required=True)
    value.add_argument("--existing-eth", required=True)
    value.add_argument("--backfill-btc", required=True)
    value.add_argument("--backfill-eth", required=True)
    value.add_argument("--design-freeze", required=True)
    value.add_argument(
        "--backfill-validation",
        required=True,
    )
    value.add_argument("--output-root", required=True)
    value.add_argument("--write", action="store_true")
    value.add_argument(
        "--overwrite",
        action="store_true",
    )
    value.add_argument("--json", action="store_true")

    return value


def main(
    argv: list[str] | None = None,
) -> int:
    args = parser().parse_args(argv)

    output_root = Path(args.output_root)
    selected_dataset_path = output_root / "official_trades_master_qlib_dataset_v1.parquet"

    try:
        artifacts = build_official_trades_master_qlib_dataset(
            master_path=args.master,
            existing_btc_path=args.existing_btc,
            existing_eth_path=args.existing_eth,
            backfill_btc_path=args.backfill_btc,
            backfill_eth_path=args.backfill_eth,
            design_freeze_path=args.design_freeze,
            backfill_validation_path=args.backfill_validation,
            selected_dataset_path=selected_dataset_path,
        )

        report = dict(artifacts.report)

        if args.write:
            persisted = persist_qlib_dataset_artifacts(
                artifacts,
                output_root=output_root,
                overwrite=bool(args.overwrite),
            )
            report["write_performed"] = True
            report["dataset_materialized"] = True
            report["persisted"] = persisted

    except QlibDatasetValidationError as exc:
        report = {
            "schema_version": "official_trades_master_qlib_dataset_v1",
            "status": "blocked",
            "reason": str(exc),
            "decision": "QLIB_DATASET_BLOCKED",
            "write_performed": False,
            "dataset_materialized": False,
            "training_performed": False,
            "model_promotion_performed": False,
            "operational_authority": False,
            "changes_risk": False,
            "sends_orders": False,
            "exchange_private_access": False,
        }

    if args.json:
        print(
            json.dumps(
                report,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
        )
    else:
        print(f"STATUS={report['status']}")
        print(f"DECISION={report.get('decision')}")
        print(f"ROWS={report.get('row_count', 0)}")
        print(f"FEATURES={report.get('feature_count', 0)}")
        print(f"LABELS={report.get('label_count', 0)}")
        print(f"LEAKAGE_STATUS={report.get('leakage_status')}")
        print(f"WRITE_PERFORMED={report.get('write_performed', False)}")

    return 0 if report["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
