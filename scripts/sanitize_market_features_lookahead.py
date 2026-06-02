from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.market.market_feature_schema import (  # noqa: E402
    lookahead_columns,
    sanitize_operational_market_features,
)


DEFAULT_INPUT = Path("data/features/market_features_60d.parquet")
DEFAULT_REPORT = Path("data/reports/sanitize_market_features_lookahead_report.json")
DEFAULT_BACKUP_DIR = Path("data/backups/market_features_lookahead_cleanup")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safety_payload() -> dict[str, Any]:
    return {
        "paper_only": True,
        "shadow_only": True,
        "runtime_mode": "paper",
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
    }


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_backup(source: Path, backup_dir: Path) -> Path:
    run_dir = backup_dir / datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    run_dir.mkdir(parents=True, exist_ok=True)
    backup = run_dir / source.name
    shutil.copy2(source, backup)
    return backup


def base_report(
    *,
    status: str,
    reason: str,
    dry_run: bool,
    apply: bool,
    input_path: Path,
    backup_path: Path | None,
    rows_before: int,
    rows_after: int,
    columns_before_count: int,
    columns_after_count: int,
    removed_columns: list[str],
    source_hash_before: str | None,
    source_hash_after: str | None,
    write_performed: bool,
) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "dry_run": bool(dry_run),
        "apply": bool(apply),
        "input_path": str(input_path),
        "backup_path": str(backup_path) if backup_path is not None else None,
        "rows_before": int(rows_before),
        "rows_after": int(rows_after),
        "columns_before_count": int(columns_before_count),
        "columns_after_count": int(columns_after_count),
        "removed_columns": sorted(removed_columns),
        "removed_columns_count": int(len(removed_columns)),
        "source_hash_before": source_hash_before,
        "source_hash_after": source_hash_after,
        "write_performed": bool(write_performed),
        "created_at": utc_now(),
        **safety_payload(),
    }


def sanitize_market_features_lookahead(
    *,
    input_path: Path = DEFAULT_INPUT,
    report_path: Path = DEFAULT_REPORT,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    apply: bool = False,
) -> dict[str, Any]:
    dry_run = not apply
    if not input_path.exists():
        report = base_report(
            status="blocked",
            reason="missing_input",
            dry_run=dry_run,
            apply=apply,
            input_path=input_path,
            backup_path=None,
            rows_before=0,
            rows_after=0,
            columns_before_count=0,
            columns_after_count=0,
            removed_columns=[],
            source_hash_before=None,
            source_hash_after=None,
            write_performed=False,
        )
        write_json(report_path, report)
        return report

    source_hash_before = file_sha256(input_path)
    frame = pd.read_parquet(input_path)
    rows_before = int(len(frame))
    columns_before_count = int(len(frame.columns))
    removed = lookahead_columns(frame)

    if not removed:
        report = base_report(
            status="ok",
            reason="no_action",
            dry_run=dry_run,
            apply=apply,
            input_path=input_path,
            backup_path=None,
            rows_before=rows_before,
            rows_after=rows_before,
            columns_before_count=columns_before_count,
            columns_after_count=columns_before_count,
            removed_columns=[],
            source_hash_before=source_hash_before,
            source_hash_after=source_hash_before,
            write_performed=False,
        )
        write_json(report_path, report)
        return report

    sanitized, _ = sanitize_operational_market_features(frame)
    rows_after = int(len(sanitized))
    columns_after_count = int(len(sanitized.columns))

    if dry_run:
        report = base_report(
            status="ok",
            reason="lookahead_columns_detected",
            dry_run=True,
            apply=False,
            input_path=input_path,
            backup_path=None,
            rows_before=rows_before,
            rows_after=rows_after,
            columns_before_count=columns_before_count,
            columns_after_count=columns_after_count,
            removed_columns=removed,
            source_hash_before=source_hash_before,
            source_hash_after=source_hash_before,
            write_performed=False,
        )
        write_json(report_path, report)
        return report

    backup_path = create_backup(input_path, backup_dir)
    tmp_output = input_path.with_suffix(input_path.suffix + ".tmp")
    sanitized.to_parquet(tmp_output, index=False)
    tmp_output.replace(input_path)
    source_hash_after = file_sha256(input_path)
    report = base_report(
        status="ok",
        reason="sanitized",
        dry_run=False,
        apply=True,
        input_path=input_path,
        backup_path=backup_path,
        rows_before=rows_before,
        rows_after=rows_after,
        columns_before_count=columns_before_count,
        columns_after_count=columns_after_count,
        removed_columns=removed,
        source_hash_before=source_hash_before,
        source_hash_after=source_hash_after,
        write_performed=True,
    )
    write_json(report_path, report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Remove future_ret_* from operational market_features_60d parquet.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_DIR))
    parser.add_argument("--apply", action="store_true", help="Overwrite input after creating a backup. Default is dry-run.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = sanitize_market_features_lookahead(
        input_path=Path(args.input),
        report_path=Path(args.report),
        backup_dir=Path(args.backup_dir),
        apply=bool(args.apply),
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
