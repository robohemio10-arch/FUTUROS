from __future__ import annotations

import importlib
from pathlib import Path

from smartcrypto.qlib_engine.common import load_config, qlib_runtime_status, write_json
from smartcrypto.qlib_engine.dataset_adapter import build_qlib_market_dataset


def main() -> None:
    required_modules = ["pandas", "pyarrow", "sklearn", "joblib", "yaml"]
    missing = []
    for module in required_modules:
        try:
            importlib.import_module(module)
        except Exception:
            missing.append(module)

    config_path = Path("config/qlib_model.yml")
    market_features_path = Path("data/features/market_features_60d.parquet")
    config = load_config(config_path)

    build_report = build_qlib_market_dataset(
        market_features_path=market_features_path,
        output_path="data/qlib/qlib_market_dataset.parquet",
        metadata_path="data/reports/phase8_qlib_dataset_report.json",
        config=config,
    )

    report = {
        "status": "ok" if not missing and build_report.get("status") == "ok" else "blocked",
        "missing_modules": missing,
        "config_exists": config_path.exists(),
        "market_features_exists": market_features_path.exists(),
        "qlib_runtime": qlib_runtime_status(),
        "dataset_build": build_report,
    }
    write_json("data/reports/phase8_preflight_report.json", report)
    print_json(report)


def print_json(payload: dict) -> None:
    import json

    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
