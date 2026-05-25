from __future__ import annotations

import json
from dataclasses import asdict

from smartcrypto.ml.market_predictions import export_latest_market_predictions


def main() -> None:
    result = export_latest_market_predictions()
    print(json.dumps(asdict(result), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
