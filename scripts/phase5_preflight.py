from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from smartcrypto.data.trader_master_fingerprint_v2.legacy_master_governance import (
    DEFAULT_MASTER,
)
from smartcrypto.data.trader_master_fingerprint_v2.master_adapter import (
    read_trader_master_readonly,
)


REQUIRED_MODULES = ["pandas", "pyarrow", "sqlalchemy", "numpy", "openpyxl"]
REQUIRED_PATHS = [
    Path("data/features/market_features_60d.parquet"),
    Path("data/sqlite/trading_dataset.sqlite"),
    Path("scripts/import_trades_incremental.py"),
    Path("scripts/rebuild_phase5_datasets.py"),
    Path("scripts/inspect_phase5_outputs.py"),
]
DIRECTORIES = [
    Path("data/trades/inbox"),
    Path("data/trades/processed"),
    Path("data/reports"),
    Path("data/evidence"),
    Path("data/tmp"),
]


def module_missing(name: str) -> bool:
    return importlib.util.find_spec(name) is None


def main() -> None:
    for directory in DIRECTORIES:
        directory.mkdir(parents=True, exist_ok=True)

    missing_modules = [name for name in REQUIRED_MODULES if module_missing(name)]
    missing_paths = [str(path) for path in REQUIRED_PATHS if not path.exists()]
    legacy_bundle = read_trader_master_readonly(
        project_root=Path.cwd(),
        trader_master_path=DEFAULT_MASTER,
    )
    if legacy_bundle.report.get("status") != "ok":
        missing_paths.append("legacy_master_readonly")

    report = {
        "status": "ok" if not missing_modules and not missing_paths else "error",
        "missing_modules": missing_modules,
        "missing_paths": missing_paths,
        "inbox_dir": "data/trades/inbox",
        "legacy_master_readonly": legacy_bundle.report,
        "compatibility_xlsx_exists": Path("data/trades/trades_excel.xlsx").exists(),
        "inbox_files": [
            path.name
            for path in sorted(Path("data/trades/inbox").glob("*"))
            if path.is_file() and not path.name.startswith("~$")
        ],
    }

    Path("data/reports").mkdir(parents=True, exist_ok=True)
    Path("data/reports/phase5_preflight_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(json.dumps(report, indent=2, ensure_ascii=False))

    if report["status"] != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
