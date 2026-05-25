from __future__ import annotations

import argparse
from pathlib import Path

from smartcrypto.data.feature_builder import build_market_features


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/raw/futures_ohlcv_60d.parquet")
    parser.add_argument("--output", default="data/features/market_features_60d.parquet")
    args = parser.parse_args()

    frame = build_market_features(
        input_path=Path(args.input),
        output_path=Path(args.output),
    )

    print(
        {
            "status": "ok",
            "rows": int(len(frame)),
            "columns": int(len(frame.columns)),
            "output": str(Path(args.output)),
        }
    )


if __name__ == "__main__":
    main()
