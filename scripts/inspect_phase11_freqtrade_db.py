from __future__ import annotations

import json

from smartcrypto.data.freqtrade_db_reader import write_db_status_report


def main() -> None:
    report = write_db_status_report()
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
