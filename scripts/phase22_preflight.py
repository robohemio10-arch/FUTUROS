from __future__ import annotations
import importlib.util, json, os
from datetime import datetime, timezone
from pathlib import Path

REQUIRED_PATHS = [
    "docker-compose.paper.yml",
    "config/phase22_historical_backfill.yml",
    "data/sqlite/trading_dataset.sqlite",
    "scripts/download_phase22_historical_candles.py",
    "scripts/build_phase22_market_features.py",
    "scripts/inspect_phase22_outputs.py",
]

def module_exists(name: str) -> bool:
    return importlib.util.find_spec(name) is not None

def env_true(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in {"1", "true", "yes", "y", "on"}

def main() -> None:
    missing_paths = [path for path in REQUIRED_PATHS if not Path(path).exists()]
    missing_modules = [name for name in ["pandas", "numpy"] if not module_exists(name)]
    unsafe_flags = {
        "LIVE_ENABLED": env_true("LIVE_ENABLED"),
        "ORDER_SUBMISSION_ENABLED": env_true("ORDER_SUBMISSION_ENABLED"),
        "REAL_ORDER_SUBMISSION_ENABLED": env_true("REAL_ORDER_SUBMISSION_ENABLED"),
    }
    for directory in ["data/raw/binance_futures_klines", "data/features", "data/reports", "data/evidence", "data/backups/phase22", "data/tmp"]:
        Path(directory).mkdir(parents=True, exist_ok=True)
    status = "ok"
    errors = []
    if missing_paths:
        status = "error"; errors.append("missing_paths")
    if missing_modules:
        status = "error"; errors.append("missing_modules")
    if any(unsafe_flags.values()):
        status = "error"; errors.append("unsafe_live_or_order_flag_enabled")
    report = {
        "status": status,
        "phase": "phase22_historical_market_backfill",
        "errors": errors,
        "missing_paths": missing_paths,
        "missing_modules": missing_modules,
        "unsafe_flags": unsafe_flags,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    Path("data/reports/phase22_preflight_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if status != "ok":
        raise SystemExit(1)

if __name__ == "__main__":
    main()
