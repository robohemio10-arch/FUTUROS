#!/usr/bin/env python3
"""Build read-only execution-intelligence Net PnL attribution evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.aibot_parity import (  # noqa: E402
    execution_intelligence_net_pnl_attribution as attribution,
)

# Keep the CLI bound to the complete Branch 11 builder.
build_execution_intelligence_net_pnl_attribution_v1 = (
    attribution.build_execution_intelligence_net_pnl_attribution_v1
)

def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--master", required=True)
    value.add_argument("--json", action="store_true")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    report = build_execution_intelligence_net_pnl_attribution_v1(
        master_path=args.master,
    )
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":") if args.json else None,
            indent=None if args.json else 2,
            allow_nan=False,
        )
    )
    return 0 if report.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
