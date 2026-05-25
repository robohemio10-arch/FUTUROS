from __future__ import annotations

import argparse
import json

from smartcrypto.execution.signal_contract_guard import repair_if_needed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    report = repair_if_needed(force=args.force)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
