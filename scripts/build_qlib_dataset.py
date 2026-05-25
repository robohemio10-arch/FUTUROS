from __future__ import annotations

import json

from smartcrypto.qlib_engine.common import load_config
from smartcrypto.qlib_engine.dataset_adapter import build_qlib_market_dataset


def main() -> None:
    config = load_config()
    report = build_qlib_market_dataset(
        market_features_path="data/features/market_features_60d.parquet",
        output_path="data/qlib/qlib_market_dataset.parquet",
        metadata_path="data/reports/phase8_qlib_dataset_report.json",
        config=config,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
