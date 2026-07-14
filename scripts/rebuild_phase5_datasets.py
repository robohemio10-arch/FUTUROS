from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from smartcrypto.data.trader_master_fingerprint_v2.legacy_master_governance import (
    DEFAULT_MASTER,
)
from smartcrypto.data.trader_master_fingerprint_v2.master_adapter import (
    MasterReadBundle,
    read_trader_master_readonly,
)


REPORT_PATH = Path("data/reports/phase5_rebuild_report.json")
OCR_MASTER_V11_SHA256 = "83e2d17db317cc84b2bd39e00a961bd8d568c4375c5a4a113f6a26df58972e90"
OCR_MASTER_V11_ROWS = 3058


def run_script(path: str) -> dict:
    completed = subprocess.run(
        [sys.executable, path],
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "script": path,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "ok": completed.returncode == 0,
    }


def table_rows(path: Path) -> int:
    if path.suffix.lower() == ".parquet":
        return int(len(pd.read_parquet(path)))
    return int(len(pd.read_excel(path)))


def validate_phase5_source_alignment(
    compatibility_xlsx: Path,
    legacy_bundle: MasterReadBundle,
) -> dict:
    errors: list[str] = []
    if not compatibility_xlsx.exists():
        errors.append("trades_excel_xlsx_not_found")
    if legacy_bundle.report.get("status") != "ok":
        errors.append(
            f"legacy_master_read_blocked:{legacy_bundle.report.get('reason', 'unknown')}"
        )

    rows: dict[str, int | None] = {
        "compatibility_xlsx": None,
        "legacy_master": None,
    }
    master_sha256 = legacy_bundle.report.get("trader_master_sha256_before")
    if not errors:
        try:
            rows = {
                "compatibility_xlsx": table_rows(compatibility_xlsx),
                "legacy_master": int(
                    legacy_bundle.report.get("trader_master_row_count", 0)
                ),
            }
        except (OSError, ValueError) as exc:
            errors.append(f"source_alignment_read_error:{type(exc).__name__}")

    if rows["legacy_master"] is not None:
        if rows["compatibility_xlsx"] != rows["legacy_master"]:
            errors.append(
                "trades_excel_rows_mismatch:"
                f"{rows['compatibility_xlsx']}!={rows['legacy_master']}"
            )
    if master_sha256 == OCR_MASTER_V11_SHA256 and rows["legacy_master"] != OCR_MASTER_V11_ROWS:
        errors.append(
            f"ocr_master_v11_rows_mismatch:{rows['legacy_master']}!={OCR_MASTER_V11_ROWS}"
        )

    return {
        "status": "ok" if not errors else "blocked",
        "reason": "phase5_source_alignment_ok" if not errors else "phase5_source_alignment_failed",
        "validation_errors": sorted(set(errors)),
        "rows": rows,
        "master_sha256": master_sha256,
        "ocr_master_v11_detected": master_sha256 == OCR_MASTER_V11_SHA256,
        "legacy_master_readonly": legacy_bundle.report,
    }


def main() -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    compatibility_xlsx = Path("data/trades/trades_excel.xlsx")
    legacy_bundle = read_trader_master_readonly(
        project_root=Path.cwd(),
        trader_master_path=DEFAULT_MASTER,
    )

    alignment = validate_phase5_source_alignment(
        compatibility_xlsx,
        legacy_bundle,
    )
    if alignment["status"] != "ok":
        report = {
            "status": "blocked",
            "reason": alignment["reason"],
            "compatibility_xlsx": str(compatibility_xlsx),
            "legacy_master_readonly": legacy_bundle.report,
            "source_alignment": alignment,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(report, indent=2, ensure_ascii=False))
        raise SystemExit(2)

    scripts = [
        "scripts/build_trade_enriched.py",
        "scripts/build_training_dataset.py",
    ]

    missing_scripts = [script for script in scripts if not Path(script).exists()]
    if missing_scripts:
        report = {
            "status": "error",
            "reason": "missing_rebuild_scripts",
            "missing_scripts": missing_scripts,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(report, indent=2, ensure_ascii=False))
        raise SystemExit(1)

    results: list[dict] = []
    output_validation_errors: list[str] = []
    expected_rows = int(alignment["rows"]["legacy_master"])

    trade_result = run_script(scripts[0])
    results.append(trade_result)
    if trade_result["ok"]:
        trade_rows = table_rows(Path("data/features/trade_enriched.parquet"))
        if trade_rows != expected_rows:
            output_validation_errors.append(
                f"trade_enriched_rows_mismatch:{trade_rows}!={expected_rows}"
            )
    if trade_result["ok"] and not output_validation_errors:
        training_result = run_script(scripts[1])
        results.append(training_result)
        if training_result["ok"]:
            training_rows = table_rows(Path("data/features/training_dataset.parquet"))
            if training_rows != expected_rows:
                output_validation_errors.append(
                    f"training_dataset_rows_mismatch:{training_rows}!={expected_rows}"
                )

    status = (
        "ok"
        if len(results) == len(scripts)
        and all(result["ok"] for result in results)
        and not output_validation_errors
        else "blocked"
    )

    report = {
        "status": status,
        "source_alignment": alignment,
        "output_validation_errors": output_validation_errors,
        "steps": results,
        "outputs": {
            "trade_enriched": "data/features/trade_enriched.parquet",
            "training_dataset": "data/features/training_dataset.parquet",
            "sqlite": "data/sqlite/trading_dataset.sqlite",
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))

    if status != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
