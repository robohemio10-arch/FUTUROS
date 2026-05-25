from __future__ import annotations

import json
from dataclasses import asdict

from smartcrypto.ml.market_model import train_market_direction_model


def main() -> None:
    result = train_market_direction_model()
    print(json.dumps(asdict(result), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
