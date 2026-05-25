from __future__ import annotations

import argparse
import json

from smartcrypto.execution.paper_exit_control import generate_exit_control


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pair", default="all")
    parser.add_argument("--validity-minutes", type=int, default=None)
    parser.add_argument("--reason", default="phase15_controlled_paper_exit")
    args = parser.parse_args()
    report = generate_exit_control(pair=args.pair, validity_minutes=args.validity_minutes, reason=args.reason)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
