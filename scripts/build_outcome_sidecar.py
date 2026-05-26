from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import json
import traceback

from smartcrypto.ml.outcome_sidecar import (
    build_outcome_sidecar,
    read_parquet,
    utc_now,
    write_json,
    write_sidecar_outputs,
)


DEFAULT_INPUT = Path("data/features/training_dataset.parquet")
DEFAULT_OUTPUT = Path("data/features/training_outcome_sidecar.parquet")
DEFAULT_REPORT = Path("data/reports/outcome_sidecar_report.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build an offline outcome sidecar for financial evaluation.",
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--id-column", default="trade_id")
    parser.add_argument("--target-column", default="target_win")
    parser.add_argument("--return-column", default="return_pct")
    parser.add_argument("--mfe-column", default="mfe_pct")
    parser.add_argument("--mae-column", default="mae_pct")
    parser.add_argument("--symbol-column", default="symbol")
    parser.add_argument("--time-column", default="open_1m_ts")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        frame = read_parquet(args.input)
        sidecar, report = build_outcome_sidecar(
            frame,
            input_path=args.input,
            output_path=args.output,
            id_column=args.id_column,
            target_column=args.target_column,
            return_column=args.return_column,
            mfe_column=args.mfe_column,
            mae_column=args.mae_column,
            symbol_column=args.symbol_column,
            time_column=args.time_column,
        )
        write_sidecar_outputs(
            sidecar=sidecar,
            report=report,
            output_path=args.output,
            report_path=args.report,
        )
    except Exception as exc:
        failure = {
            "status": "FAILED",
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "runtime_mode": "research",
            "live_enabled": False,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "created_at": utc_now(),
        }
        write_json(args.report, failure)
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
