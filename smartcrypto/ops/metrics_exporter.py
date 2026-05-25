from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    signals_path = Path("data/freqtrade_signals.json")
    signal_count = 0

    if signals_path.exists():
        payload = json.loads(signals_path.read_text(encoding="utf-8"))
        signal_count = len(payload.get("signals", []))

    print(f"smartcrypto_signal_count {signal_count}")
    print(f"smartcrypto_metrics_timestamp {int(datetime.now(timezone.utc).timestamp())}")


if __name__ == "__main__":
    main()
