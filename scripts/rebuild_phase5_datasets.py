from __future__ import annotations

import json
import hashlib
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def table_rows(path: Path) -> int:
    if path.suffix.lower() == ".parquet":
        return int(len(pd.read_parquet(path)))
    return int(len(pd.read_excel(path)))


def validate_phase5_source_alignment(
    master_xlsx: Path,
    compatibility_xlsx: Path,
    master_parquet: Path,
) -> dict:
    errors: list[str] = []
    for label, path in (
        ("trades_master_xlsx", master_xlsx),
        ("trades_excel_xlsx", compatibility_xlsx),
        ("trades_master_parquet", master_parquet),
    ):
        if not path.exists():
            errors.append(f"{label}_not_found")

    rows: dict[str, int | None] = {
        "master_xlsx": None,
        "compatibility_xlsx": None,
        "master_parquet": None,
    }
    master_sha256: str | None = None
    if not errors:
        try:
            rows = {
                "master_xlsx": table_rows(master_xlsx),
                "compatibility_xlsx": table_rows(compatibility_xlsx),
                "master_parquet": table_rows(master_parquet),
            }
            master_sha256 = sha256_file(master_xlsx)
        except (OSError, ValueError) as exc:
            errors.append(f"source_alignment_read_error:{type(exc).__name__}")

    if rows["master_xlsx"] is not None:
        if rows["compatibility_xlsx"] != rows["master_xlsx"]:
            errors.append(
                "trades_excel_rows_mismatch:"
                f"{rows['compatibility_xlsx']}!={rows['master_xlsx']}"
            )
        if rows["master_parquet"] != rows["master_xlsx"]:
            errors.append(
                "trades_master_parquet_rows_mismatch:"
                f"{rows['master_parquet']}!={rows['master_xlsx']}"
            )
    if master_sha256 == OCR_MASTER_V11_SHA256 and rows["master_xlsx"] != OCR_MASTER_V11_ROWS:
        errors.append(
            f"ocr_master_v11_rows_mismatch:{rows['master_xlsx']}!={OCR_MASTER_V11_ROWS}"
        )

    return {
        "status": "ok" if not errors else "blocked",
        "reason": "phase5_source_alignment_ok" if not errors else "phase5_source_alignment_failed",
        "validation_errors": sorted(set(errors)),
        "rows": rows,
        "master_sha256": master_sha256,
        "ocr_master_v11_detected": master_sha256 == OCR_MASTER_V11_SHA256,
    }


def main() -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    compatibility_xlsx = Path("data/trades/trades_excel.xlsx")
    master_xlsx = Path("data/trades/trades_master.xlsx")
    master_parquet = Path("data/trades/trades_master.parquet")

    alignment = validate_phase5_source_alignment(
        master_xlsx,
        compatibility_xlsx,
        master_parquet,
    )
    if alignment["status"] != "ok":
        report = {
            "status": "blocked",
            "reason": alignment["reason"],
            "compatibility_xlsx": str(compatibility_xlsx),
            "master_xlsx_exists": master_xlsx.exists(),
            "master_parquet_exists": master_parquet.exists(),
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
    expected_rows = int(alignment["rows"]["master_xlsx"])

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
