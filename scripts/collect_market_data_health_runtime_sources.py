from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.market_data.health_runtime_sources import (  # noqa: E402
    DEFAULT_OUTPUT_DIR,
    DEFAULT_REPORT_PATH,
    DEFAULT_SYMBOLS,
    collect_market_data_health_runtime_sources,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect public market-data health runtime sources in paper/shadow mode.")
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = collect_market_data_health_runtime_sources(
        symbols=args.symbols,
        output_dir=args.output_dir,
        report_path=args.report,
        timeout_seconds=args.timeout_seconds,
        strict=args.strict,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if report.get("status") == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
