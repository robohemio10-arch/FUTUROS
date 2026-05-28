from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.qlib_engine.fresh_prediction_runner import run_qlib_fresh_predictions


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gera predições Qlib frescas para o fluxo paper/shadow.")
    parser.add_argument("--market-features", default="data/features/market_features_60d.parquet")
    parser.add_argument("--model", default="data/models/qlib_market_model.joblib")
    parser.add_argument("--output", default="data/predictions/latest_qlib_predictions.parquet")
    parser.add_argument("--report", default="data/reports/qlib_fresh_prediction_runner_report.json")
    parser.add_argument("--config", default="config/qlib_model.yml")
    parser.add_argument("--max-allowed-age-minutes", type=float, default=90)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = run_qlib_fresh_predictions(
        market_features_path=args.market_features,
        model_path=args.model,
        output_path=args.output,
        report_path=args.report,
        config_path=args.config,
        max_allowed_age_minutes=args.max_allowed_age_minutes,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
