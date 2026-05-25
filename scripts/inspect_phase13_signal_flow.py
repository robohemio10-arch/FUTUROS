from __future__ import annotations

import argparse
import json

from smartcrypto.execution.signal_producer import inspect_signal_runtime


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/signal_producer.yml")
    args = parser.parse_args()

    report = inspect_signal_runtime(args.config)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    primary_count = report.get("primary_signal", {}).get("active_signal_count", 0)
    pinned_count = report.get("pinned_signal", {}).get("active_signal_count", 0)
    if primary_count <= 0 and pinned_count <= 0:
        raise SystemExit("VALIDATION_BLOCKED: no active signals in primary or pinned signal files")
    print("VALIDATION_OK")


if __name__ == "__main__":
    main()
