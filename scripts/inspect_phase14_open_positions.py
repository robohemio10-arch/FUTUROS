from __future__ import annotations

import json

from smartcrypto.data.paper_trade_lifecycle import inspect_open_positions


def main() -> None:
    report = inspect_open_positions()
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
