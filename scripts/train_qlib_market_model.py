from __future__ import annotations

import json

from smartcrypto.qlib_engine.common import load_config
from smartcrypto.qlib_engine.predictor import train_qlib_market_model


def main() -> None:
    config = load_config()
    report = train_qlib_market_model(
        dataset_path="data/qlib/qlib_market_dataset.parquet",
        model_output_path="data/models/qlib_market_model.joblib",
        report_path="data/reports/phase8_qlib_training_report.json",
        config=config,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
