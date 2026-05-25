from __future__ import annotations

import argparse
import json
from pathlib import Path

from smartcrypto.data.trades_importer import import_trades_incrementally


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Importa lotes OCR incrementalmente para trades_master.")
    parser.add_argument("--inbox-dir", default="data/trades/inbox")
    parser.add_argument("--master-xlsx", default="data/trades/trades_master.xlsx")
    parser.add_argument("--master-parquet", default="data/trades/trades_master.parquet")
    parser.add_argument("--compatibility-xlsx", default="data/trades/trades_excel.xlsx")
    parser.add_argument("--processed-dir", default="data/trades/processed")
    parser.add_argument("--report", default="data/reports/phase5_import_report.json")
    parser.add_argument("--no-archive", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = import_trades_incrementally(
        inbox_dir=Path(args.inbox_dir),
        master_xlsx_path=Path(args.master_xlsx),
        master_parquet_path=Path(args.master_parquet),
        compatibility_xlsx_path=Path(args.compatibility_xlsx),
        processed_dir=Path(args.processed_dir),
        report_path=Path(args.report),
        archive=not args.no_archive,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
