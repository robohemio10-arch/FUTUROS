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

from smartcrypto.data.trades_importer import (
    CANONICAL_COLUMNS,
    REQUIRED_COLUMNS,
    build_dedup_key,
    clean_trade_frame,
    normalize_columns,
    read_master,
    read_trade_file,
    write_master,
)


REPORT_PATH = Path("data/reports/large_trades_import_preflight_report.json")
ALLOWED_SYMBOLS = {"BTCUSDT", "ETHUSDT", "BTC_USDT", "ETH_USDT", "BTC/USDT", "ETH/USDT"}
VALID_SIDE_TOKENS = {"LONG", "SHORT", "BUY", "SELL"}
NUMERIC_REQUIRED_COLUMNS = ["pnl_fechado", "preco_abertura", "preco_fechamento"]
TIME_COLUMNS = ["horario_abertura", "horario_fechamento"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preflight/dry-run quality gate para importar lote grande de trades.",
    )
    parser.add_argument("--source-file", required=True)
    parser.add_argument("--master-xlsx", default="data/trades/trades_master.xlsx")
    parser.add_argument("--master-parquet", default="data/trades/trades_master.parquet")
    parser.add_argument("--compatibility-xlsx", default="data/trades/trades_excel.xlsx")
    parser.add_argument("--report", default=str(REPORT_PATH))
    parser.add_argument("--backup-dir", default="data/backups/large_trades_import")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-preflight", default=str(REPORT_PATH))
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_number_series(series: pd.Series) -> pd.Series:
    def normalize_value(value: object) -> str:
        if pd.isna(value):
            return ""
        text = str(value).strip()
        text = pd.Series([text]).str.replace(r"[R$\s%]", "", regex=True).iloc[0]
        if "," in text:
            return text.replace(".", "").replace(",", ".")
        return text

    return pd.to_numeric(series.map(normalize_value), errors="coerce")


def normalize_symbol(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip().upper().replace("/USDT:USDT", "USDT")


def normalize_side(value: object) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip().upper()
    for token in VALID_SIDE_TOKENS:
        if token in text:
            return token
    return text


def read_source(source_file: Path) -> tuple[pd.DataFrame | None, list[str]]:
    if not source_file.exists():
        return None, [f"source_file_missing:{source_file}"]
    try:
        raw = read_trade_file(source_file)
        return raw, []
    except Exception as exc:
        return None, [f"source_file_unreadable:{source_file}:{exc}"]


def validate_large_trade_source(raw: pd.DataFrame, source_file: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    normalized = normalize_columns(raw).dropna(how="all").copy()
    missing_required = [column for column in REQUIRED_COLUMNS if column not in normalized.columns]
    cleaned = clean_trade_frame(raw, source_file=source_file.name)

    parsed_open = pd.to_datetime(cleaned["horario_abertura"], utc=True, errors="coerce")
    parsed_close = pd.to_datetime(cleaned["horario_fechamento"], utc=True, errors="coerce")
    symbols = cleaned["moeda"].map(normalize_symbol)
    sides = cleaned["fechar_side"].map(normalize_side)
    pnl = parse_number_series(cleaned["pnl_fechado"])
    entry = parse_number_series(cleaned["preco_abertura"])
    exit_ = parse_number_series(cleaned["preco_fechamento"])

    required_empty = pd.Series(False, index=cleaned.index)
    for column in REQUIRED_COLUMNS:
        required_empty |= cleaned[column].astype("string").str.strip().fillna("").eq("")

    invalid_dates = parsed_open.isna() | parsed_close.isna() | (parsed_close < parsed_open)
    invalid_symbols = ~symbols.isin(ALLOWED_SYMBOLS)
    invalid_sides = ~sides.isin(VALID_SIDE_TOKENS)
    invalid_numeric = pnl.isna() | entry.isna() | exit_.isna() | (entry <= 0) | (exit_ <= 0)
    invalid_mask = required_empty | invalid_dates | invalid_symbols | invalid_sides | invalid_numeric

    blocking_errors: list[str] = []
    warnings: list[str] = []
    if missing_required:
        blocking_errors.append(f"missing_required_columns:{missing_required}")
    if int(required_empty.sum()):
        blocking_errors.append(f"empty_required_rows:{int(required_empty.sum())}")
    if int(invalid_dates.sum()):
        blocking_errors.append(f"invalid_date_rows:{int(invalid_dates.sum())}")
    if int(invalid_symbols.sum()):
        blocking_errors.append(f"invalid_symbol_rows:{int(invalid_symbols.sum())}")
    if int(invalid_sides.sum()):
        blocking_errors.append(f"invalid_side_rows:{int(invalid_sides.sum())}")
    if int(invalid_numeric.sum()):
        blocking_errors.append(f"invalid_numeric_rows:{int(invalid_numeric.sum())}")
    if cleaned["order_id"].astype("string").str.strip().fillna("").eq("").any():
        warnings.append("missing_order_id_rows_use_fingerprint_dedup")

    valid = cleaned.loc[~invalid_mask].copy()
    valid["horario_abertura"] = parsed_open.loc[valid.index].dt.strftime("%Y-%m-%d %H:%M:%S")
    valid["horario_fechamento"] = parsed_close.loc[valid.index].dt.strftime("%Y-%m-%d %H:%M:%S")

    validation = {
        "missing_required_columns": missing_required,
        "invalid_rows": int(len(cleaned) if missing_required else invalid_mask.sum()),
        "invalid_date_rows": int(invalid_dates.sum()),
        "invalid_symbol_rows": int(invalid_symbols.sum()),
        "invalid_side_rows": int(invalid_sides.sum()),
        "invalid_numeric_rows": int(invalid_numeric.sum()),
        "symbols": sorted(symbols.loc[~symbols.eq("")].dropna().unique().tolist()),
        "sides": sorted(sides.loc[~sides.eq("")].dropna().unique().tolist()),
        "min_trade_ts": parsed_open.dropna().min().isoformat() if not parsed_open.dropna().empty else None,
        "max_trade_ts": parsed_close.dropna().max().isoformat() if not parsed_close.dropna().empty else None,
        "blocking_errors": blocking_errors,
        "warnings": warnings,
    }
    return valid, validation


def add_dedup_keys(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    if "_dedup_key" not in result.columns:
        result["_dedup_key"] = result.apply(build_dedup_key, axis=1) if len(result) else pd.Series(dtype="string")
    return result


def build_preflight_report(
    *,
    source_file: Path,
    master_xlsx_path: Path,
    master_parquet_path: Path,
    compatibility_xlsx_path: Path,
    report_path: Path,
    apply: bool,
    backup_dir: Path,
    confirm_preflight_path: Path,
) -> tuple[dict[str, Any], pd.DataFrame | None, pd.DataFrame | None]:
    raw, read_errors = read_source(source_file)
    source_hash = file_sha256(source_file) if source_file.exists() else None
    master = read_master(master_parquet_path, master_xlsx_path)
    previous_master_rows = int(len(master))

    if raw is None:
        report = base_report(
            status="blocked",
            reason="source_read_failed",
            source_file=source_file,
            source_hash=source_hash,
            report_path=report_path,
            apply=apply,
            read_rows=0,
            previous_master_rows=previous_master_rows,
            candidate_new_rows=0,
            duplicate_rows=0,
            invalid_rows=0,
            final_expected_master_rows=previous_master_rows,
            blocking_errors=read_errors,
        )
        return report, None, None

    valid_incoming, validation = validate_large_trade_source(raw, source_file)
    master = add_dedup_keys(master)
    incoming = add_dedup_keys(valid_incoming)
    incoming_before_internal_dedup = len(incoming)
    incoming = incoming.drop_duplicates(subset=["_dedup_key"], keep="last")
    internal_duplicates = incoming_before_internal_dedup - len(incoming)
    existing_keys = set(master["_dedup_key"].dropna().astype(str).tolist()) if len(master) else set()
    new_rows = incoming.loc[~incoming["_dedup_key"].astype(str).isin(existing_keys)].copy()
    duplicate_existing_rows = len(incoming) - len(new_rows)
    duplicate_rows = int(internal_duplicates + duplicate_existing_rows)
    final_expected_master_rows = int(previous_master_rows + len(new_rows))

    blocking_errors = list(validation["blocking_errors"])
    reason = "ok"
    status = "ok"
    if blocking_errors:
        status = "blocked"
        reason = "validation_failed"
    elif len(new_rows) == 0:
        reason = "all_rows_duplicate"

    report = base_report(
        status=status,
        reason=reason,
        source_file=source_file,
        source_hash=source_hash,
        report_path=report_path,
        apply=apply,
        read_rows=int(len(raw)),
        previous_master_rows=previous_master_rows,
        candidate_new_rows=int(len(new_rows)),
        duplicate_rows=duplicate_rows,
        invalid_rows=int(validation["invalid_rows"]),
        final_expected_master_rows=final_expected_master_rows,
        min_trade_ts=validation["min_trade_ts"],
        max_trade_ts=validation["max_trade_ts"],
        symbols=validation["symbols"],
        sides=validation["sides"],
        blocking_errors=blocking_errors,
        warnings=validation["warnings"],
        backup_dir=backup_dir,
        confirm_preflight_path=confirm_preflight_path,
        master_xlsx_path=master_xlsx_path,
        master_parquet_path=master_parquet_path,
        compatibility_xlsx_path=compatibility_xlsx_path,
    )
    return report, master, new_rows


def base_report(
    *,
    status: str,
    reason: str,
    source_file: Path,
    source_hash: str | None,
    report_path: Path,
    apply: bool,
    read_rows: int,
    previous_master_rows: int,
    candidate_new_rows: int,
    duplicate_rows: int,
    invalid_rows: int,
    final_expected_master_rows: int,
    min_trade_ts: str | None = None,
    max_trade_ts: str | None = None,
    symbols: list[str] | None = None,
    sides: list[str] | None = None,
    blocking_errors: list[str] | None = None,
    warnings: list[str] | None = None,
    backup_dir: Path | None = None,
    confirm_preflight_path: Path | None = None,
    master_xlsx_path: Path | None = None,
    master_parquet_path: Path | None = None,
    compatibility_xlsx_path: Path | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "mode": "apply" if apply else "dry_run",
        "dry_run": not apply,
        "source_file": str(source_file),
        "source_hash_sha256": source_hash,
        "read_rows": int(read_rows),
        "previous_master_rows": int(previous_master_rows),
        "candidate_new_rows": int(candidate_new_rows),
        "duplicate_rows": int(duplicate_rows),
        "invalid_rows": int(invalid_rows),
        "final_expected_master_rows": int(final_expected_master_rows),
        "min_trade_ts": min_trade_ts,
        "max_trade_ts": max_trade_ts,
        "symbols": symbols or [],
        "sides": sides or [],
        "blocking_errors": blocking_errors or [],
        "warnings": warnings or [],
        "report_path": str(report_path),
        "backup_dir": str(backup_dir) if backup_dir is not None else None,
        "confirm_preflight_path": str(confirm_preflight_path) if confirm_preflight_path is not None else None,
        "master_xlsx": str(master_xlsx_path) if master_xlsx_path is not None else None,
        "master_parquet": str(master_parquet_path) if master_parquet_path is not None else None,
        "compatibility_xlsx": str(compatibility_xlsx_path) if compatibility_xlsx_path is not None else None,
        "backup_created": False,
        "backup_paths": [],
        "write_performed": False,
        "runtime_mode": "paper",
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "created_at": utc_now(),
    }


def validate_saved_preflight(report: dict[str, Any], preflight_path: Path) -> list[str]:
    if not preflight_path.exists():
        return [f"preflight_report_missing:{preflight_path}"]
    try:
        saved = json.loads(preflight_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"preflight_report_unreadable:{preflight_path}:{exc}"]
    errors = []
    if saved.get("status") != "ok":
        errors.append(f"preflight_status_not_ok:{saved.get('status')}")
    if saved.get("dry_run") is not True:
        errors.append("preflight_report_not_dry_run")
    for key in ["source_file", "source_hash_sha256", "candidate_new_rows", "final_expected_master_rows"]:
        if saved.get(key) != report.get(key):
            errors.append(f"preflight_mismatch:{key}")
    return errors


def create_backups(paths: list[Path], backup_dir: Path) -> list[str]:
    run_dir = backup_dir / datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    for path in paths:
        if path.exists():
            destination = run_dir / path.name
            shutil.copy2(path, destination)
            copied.append(str(destination))
    return copied


def apply_import(
    *,
    report: dict[str, Any],
    master: pd.DataFrame,
    new_rows: pd.DataFrame,
    master_xlsx_path: Path,
    master_parquet_path: Path,
    compatibility_xlsx_path: Path,
    backup_dir: Path,
) -> dict[str, Any]:
    if report["status"] != "ok":
        report["status"] = "blocked"
        report["reason"] = "preflight_failed"
        return report
    if len(new_rows) == 0:
        report["write_performed"] = False
        report["reason"] = "all_rows_duplicate"
        return report
    backup_paths = create_backups(
        [master_xlsx_path, master_parquet_path, compatibility_xlsx_path],
        backup_dir,
    )
    if not backup_paths and (master_xlsx_path.exists() or master_parquet_path.exists() or compatibility_xlsx_path.exists()):
        report["status"] = "blocked"
        report["reason"] = "backup_required_before_write"
        report["blocking_errors"] = [*report["blocking_errors"], "backup_required_before_write"]
        return report
    combined = pd.concat([master, new_rows], ignore_index=True, sort=False)
    combined = combined.drop_duplicates(subset=["_dedup_key"], keep="last")
    combined = combined.sort_values(["horario_abertura", "order_id"], na_position="last").reset_index(drop=True)
    write_master(combined, master_xlsx_path, master_parquet_path, compatibility_xlsx_path)
    report["backup_created"] = True
    report["backup_paths"] = backup_paths
    report["write_performed"] = True
    report["final_master_rows"] = int(len(combined))
    return report


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


def run_quality_gate(
    *,
    source_file: Path,
    master_xlsx_path: Path,
    master_parquet_path: Path,
    compatibility_xlsx_path: Path,
    report_path: Path,
    backup_dir: Path,
    apply: bool = False,
    confirm_preflight_path: Path | None = None,
) -> dict[str, Any]:
    confirm_preflight_path = confirm_preflight_path or report_path
    report, master, new_rows = build_preflight_report(
        source_file=source_file,
        master_xlsx_path=master_xlsx_path,
        master_parquet_path=master_parquet_path,
        compatibility_xlsx_path=compatibility_xlsx_path,
        report_path=report_path,
        apply=apply,
        backup_dir=backup_dir,
        confirm_preflight_path=confirm_preflight_path,
    )
    if apply:
        errors = validate_saved_preflight(report, confirm_preflight_path)
        if errors:
            report["status"] = "blocked"
            report["reason"] = "dry_run_required_before_apply"
            report["blocking_errors"] = [*report["blocking_errors"], *errors]
        elif master is not None and new_rows is not None:
            report = apply_import(
                report=report,
                master=master,
                new_rows=new_rows,
                master_xlsx_path=master_xlsx_path,
                master_parquet_path=master_parquet_path,
                compatibility_xlsx_path=compatibility_xlsx_path,
                backup_dir=backup_dir,
            )
    write_report(report_path, report)
    return report


def main() -> int:
    args = parse_args()
    report = run_quality_gate(
        source_file=Path(args.source_file),
        master_xlsx_path=Path(args.master_xlsx),
        master_parquet_path=Path(args.master_parquet),
        compatibility_xlsx_path=Path(args.compatibility_xlsx),
        report_path=Path(args.report),
        backup_dir=Path(args.backup_dir),
        apply=bool(args.apply),
        confirm_preflight_path=Path(args.confirm_preflight),
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
