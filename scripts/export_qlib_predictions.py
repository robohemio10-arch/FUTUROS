from __future__ import annotations

import json

from smartcrypto.qlib_engine.common import load_config
from smartcrypto.qlib_engine.predictor import export_latest_qlib_predictions


def main() -> None:
    config = load_config()
    report = export_latest_qlib_predictions(
        market_features_path="data/features/market_features_60d.parquet",
        model_path="data/models/qlib_market_model.joblib",
        output_path="data/predictions/latest_qlib_predictions.parquet",
        report_path="data/reports/phase8_qlib_prediction_report.json",
        config=config,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
