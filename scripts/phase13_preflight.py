from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import yaml


REQUIRED_PATHS = {
    "config": Path("config/signal_producer.yml"),
    "predictions": Path("data/predictions/latest_qlib_predictions.parquet"),
    "signal_producer": Path("smartcrypto/execution/signal_producer.py"),
    "primary_signals": Path("data/freqtrade_signals.json"),
}


def main() -> None:
    missing_modules = [name for name in ["pandas", "pyarrow", "yaml"] if importlib.util.find_spec(name) is None]
    paths = {name: {"path": str(path), "exists": path.exists()} for name, path in REQUIRED_PATHS.items()}
    config = None
    if REQUIRED_PATHS["config"].exists():
        config = yaml.safe_load(REQUIRED_PATHS["config"].read_text(encoding="utf-8"))

    missing_paths = [name for name, path in REQUIRED_PATHS.items() if not path.exists() and name != "primary_signals"]
    status = "ok" if not missing_modules and not missing_paths else "blocked"

    print(json.dumps({
        "status": status,
        "missing_modules": missing_modules,
        "missing_paths": missing_paths,
        "paths": paths,
        "config": config,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
