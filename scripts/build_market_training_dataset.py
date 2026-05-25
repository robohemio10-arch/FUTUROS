from __future__ import annotations

import json
from smartcrypto.ml.market_dataset import build_market_training_dataset


def main() -> None:
    result = build_market_training_dataset()
    print(json.dumps(result.__dict__, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
