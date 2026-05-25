from __future__ import annotations

import argparse
from pathlib import Path

from smartcrypto.data.binance_downloader import download_ohlcv


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--timeframes", nargs="+", default=["1m", "5m"])
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--output-dir", default="data/raw")
    args = parser.parse_args()

    frame = download_ohlcv(
        symbols=args.symbols,
        timeframes=args.timeframes,
        days=args.days,
        output_dir=Path(args.output_dir),
    )

    print(
        {
            "status": "ok",
            "rows": int(len(frame)),
            "symbols": sorted(frame["symbol"].unique().tolist()),
            "timeframes": sorted(frame["tf"].unique().tolist()),
            "output_dir": str(Path(args.output_dir)),
        }
    )


if __name__ == "__main__":
    main()
