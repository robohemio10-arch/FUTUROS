from __future__ import annotations

import argparse
import json

from smartcrypto.execution.signal_producer import build_active_signals


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/signal_producer.yml")
    parser.add_argument("--force-from-predictions", action="store_true")
    parser.add_argument("--validity-minutes", type=int, default=None)
    args = parser.parse_args()

    report = build_active_signals(
        config_path=args.config,
        force_from_predictions=args.force_from_predictions,
        validity_minutes=args.validity_minutes,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
