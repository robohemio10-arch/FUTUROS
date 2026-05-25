from __future__ import annotations

import json

from smartcrypto.data.paper_trade_lifecycle import inspect_outputs


def main() -> None:
    report = inspect_outputs()
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    print("VALIDATION_OK")


if __name__ == "__main__":
    main()
