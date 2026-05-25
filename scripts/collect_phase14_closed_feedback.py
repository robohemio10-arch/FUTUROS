from __future__ import annotations

import json

from smartcrypto.data.paper_trade_lifecycle import collect_closed_feedback


def main() -> None:
    report = collect_closed_feedback()
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
