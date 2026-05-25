from __future__ import annotations

import argparse
import json
import sys

from smartcrypto.execution.paper_force_close import force_close_open_paper_trades


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pair", default="all")
    parser.add_argument("--config", default="config/paper_force_close.yml")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = force_close_open_paper_trades(pair=args.pair, config_path=args.config)
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    if report.get("status") != "ok":
        sys.exit(2)


if __name__ == "__main__":
    main()
