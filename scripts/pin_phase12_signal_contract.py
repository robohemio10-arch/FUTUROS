from __future__ import annotations

import argparse
import json
from pathlib import Path

from smartcrypto.execution.signal_store import pin_signal_contract


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--primary", default="data/freqtrade_signals.json")
    parser.add_argument("--pinned", default="data/runtime/active_freqtrade_signals.json")
    parser.add_argument("--validity-minutes", type=int, default=30)
    args = parser.parse_args()

    report = pin_signal_contract(Path(args.primary), Path(args.pinned), validity_minutes=args.validity_minutes)
    Path("data/reports").mkdir(parents=True, exist_ok=True)
    Path("data/reports/phase12_signal_pin_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
