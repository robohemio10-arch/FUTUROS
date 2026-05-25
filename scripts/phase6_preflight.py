from __future__ import annotations

import importlib
import json
from pathlib import Path

from smartcrypto.ml.market_dataset import build_market_training_dataset, load_market_model_config


def main() -> None:
    required_modules = ["pandas", "pyarrow", "sklearn", "joblib", "yaml", "numpy"]
    missing_modules = [name for name in required_modules if importlib.util.find_spec(name) is None]
    config_path = Path("config/market_model.yml")
    source_path = Path("data/features/market_features_60d.parquet")

    payload = {
        "status": "ok" if not missing_modules and config_path.exists() and source_path.exists() else "blocked",
        "missing_modules": missing_modules,
        "config_exists": config_path.exists(),
        "market_features_exists": source_path.exists(),
        "market_features_path": str(source_path),
    }

    if not missing_modules and config_path.exists() and source_path.exists():
        config = load_market_model_config(config_path)
        payload["config"] = {
            "timeframe": config["market_model"].get("timeframe"),
            "target_horizon": config["market_model"].get("target_horizon"),
            "min_rows_for_training": config["market_model"].get("min_rows_for_training"),
        }
        result = build_market_training_dataset(config_path)
        payload["training_dataset_build"] = result.__dict__

    Path("data/reports").mkdir(parents=True, exist_ok=True)
    Path("data/reports/phase6_preflight_report.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
