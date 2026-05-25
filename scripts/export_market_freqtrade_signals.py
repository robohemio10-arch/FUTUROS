from __future__ import annotations

import json
from dataclasses import asdict

from smartcrypto.execution.market_signal_exporter import export_market_signals


def main() -> None:
    result = export_market_signals()
    print(json.dumps(asdict(result), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
