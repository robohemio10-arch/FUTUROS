from __future__ import annotations

import argparse
from pathlib import Path

from smartcrypto.data.binance_downloader import download_ohlcv
from smartcrypto.data.feature_builder import build_market_features
from smartcrypto.data.sqlite_builder import build_sqlite
from smartcrypto.data.trade_enricher import build_trade_enriched


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--timeframes", nargs="+", default=["1m", "5m"])
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--trades", default="data/trades/trades_excel.xlsx")
    parser.add_argument("--trades-timezone", default="America/Sao_Paulo")
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--skip-enrichment", action="store_true")
    args = parser.parse_args()

    raw_path = Path("data/raw") / f"futures_ohlcv_{args.days}d.parquet"
    features_path = Path("data/features") / f"market_features_{args.days}d.parquet"
    database_path = Path("data/sqlite/trading_dataset.sqlite")
    enriched_path = Path("data/features/trade_enriched.parquet")
    trades_path = Path(args.trades)

    if not args.skip_download:
        download_ohlcv(
            symbols=args.symbols,
            timeframes=args.timeframes,
            days=args.days,
            output_dir=Path("data/raw"),
        )

    build_market_features(raw_path, features_path)
    build_sqlite(features_path, trades_path, database_path)

    if not args.skip_enrichment and trades_path.exists():
        build_trade_enriched(
            trades_path=trades_path,
            market_features_path=features_path,
            raw_ohlcv_path=raw_path,
            output_path=enriched_path,
            database_path=database_path,
            trades_timezone=args.trades_timezone,
        )

    print(
        {
            "status": "ok",
            "raw": str(raw_path),
            "features": str(features_path),
            "database": str(database_path),
            "enriched": str(enriched_path) if enriched_path.exists() else None,
        }
    )


if __name__ == "__main__":
    main()
