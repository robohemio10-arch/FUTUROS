from __future__ import annotations

import json
from dataclasses import asdict

from smartcrypto.data.freqtrade_history import collect_freqtrade_paper_history


def main() -> None:
    report = collect_freqtrade_paper_history()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
