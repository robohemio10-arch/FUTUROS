from __future__ import annotations

import argparse
from pathlib import Path

from smartcrypto.data.sqlite_builder import build_sqlite


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-features", default="data/features/market_features_60d.parquet")
    parser.add_argument("--trades", default="data/trades/trades_excel.xlsx")
    parser.add_argument("--output-db", default="data/sqlite/trading_dataset.sqlite")
    args = parser.parse_args()

    result = build_sqlite(
        market_features_path=Path(args.market_features),
        trades_path=Path(args.trades),
        output_db=Path(args.output_db),
    )

    print({"status": "ok", "database": str(result)})


if __name__ == "__main__":
    main()
