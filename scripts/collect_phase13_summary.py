from __future__ import annotations

import argparse
import json

from smartcrypto.execution.signal_producer import write_phase13_summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/signal_producer.yml")
    args = parser.parse_args()

    report = write_phase13_summary(args.config)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
