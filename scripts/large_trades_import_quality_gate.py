from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.data.trade_file_readonly import (  # noqa: E402
    REQUIRED_COLUMNS,
    build_dedup_key,
    clean_trade_frame,
    normalize_columns,
    read_trade_file,
)
from smartcrypto.data.trader_master_fingerprint_v2.legacy_master_governance import (  # noqa: E402
    DEFAULT_MASTER,
)
from smartcrypto.data.trader_master_fingerprint_v2.master_adapter import (  # noqa: E402
    read_trader_master_readonly,
)


REPORT_PATH = Path("data/reports/large_trades_import_preflight_report.json")
ALLOWED_SYMBOLS = {"BTCUSDT", "ETHUSDT", "BTC_USDT", "ETH_USDT", "BTC/USDT", "ETH/USDT"}
VALID_SIDE_TOKENS = {"LONG", "SHORT", "BUY", "SELL"}
NUMERIC_REQUIRED_COLUMNS = ["pnl_fechado", "preco_abertura", "preco_fechamento"]
TIME_COLUMNS = ["horario_abertura", "horario_fechamento"]
DEDUP_POLICY = "order_id_first_then_fingerprint"


def infer_master_project_root(master_parquet_path: Path) -> Path:
    if not master_parquet_path.is_absolute():
        return PROJECT_ROOT
    source = master_parquet_path.resolve()
    if source.parent.name == "trades" and source.parent.parent.name == "data":
        return source.parent.parent.parent
    return source.parent.parent


def read_master_readonly(
    master_parquet_path: Path,
) -> tuple[pd.DataFrame | None, dict[str, Any]]:
    bundle = read_trader_master_readonly(
        project_root=infer_master_project_root(master_parquet_path),
        trader_master_path=master_parquet_path,
    )
    if bundle.report.get("status") != "ok":
        return None, dict(bundle.report)
    return pd.DataFrame(bundle.source_rows), dict(bundle.report)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preflight/dry-run quality gate para importar lote grande de trades.",
    )
    parser.add_argument("--source-file", required=True)
    parser.add_argument("--master-xlsx", default=str(DEFAULT_MASTER.with_suffix(".xlsx")))
    parser.add_argument("--master-parquet", default=str(DEFAULT_MASTER))
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


def normalize_order_id(value: object) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))

    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "<na>", "nat"}:
        return ""

    excel_integer = re.fullmatch(r"([+-]?\d+)\.0+", text)
    if excel_integer:
        return excel_integer.group(1)
    return text


def build_fingerprint_key(row: pd.Series) -> str:
    fingerprint_row = row.copy()
    fingerprint_row["order_id"] = ""
    return build_dedup_key(fingerprint_row)


def build_dedup_identity(row: pd.Series) -> tuple[str, str, str]:
    normalized_order_id = normalize_order_id(row.get("order_id"))
    if normalized_order_id:
        return "order_id", f"order_id::{normalized_order_id}", normalized_order_id
    return "fingerprint", build_fingerprint_key(row), ""


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
    if cleaned["order_id"].map(normalize_order_id).eq("").any():
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
    if len(result):
        identities = result.apply(build_dedup_identity, axis=1, result_type="expand")
        result["_dedup_source"] = identities[0].astype("string")
        result["_dedup_key"] = identities[1].astype("string")
        result["_normalized_order_id"] = identities[2].astype("string")
    else:
        result["_dedup_source"] = pd.Series(dtype="string")
        result["_dedup_key"] = pd.Series(dtype="string")
        result["_normalized_order_id"] = pd.Series(dtype="string")
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
    if raw is None:
        report = base_report(
            status="blocked",
            reason="source_read_failed",
            source_file=source_file,
            source_hash=source_hash,
            report_path=report_path,
            apply=apply,
            read_rows=0,
            previous_master_rows=0,
            candidate_new_rows=0,
            duplicate_rows=0,
            duplicate_by_order_id_rows=0,
            duplicate_by_fingerprint_rows=0,
            missing_order_id_rows=0,
            invalid_rows=0,
            final_expected_master_rows=0,
            blocking_errors=read_errors,
        )
        return report, None, None

    valid_incoming, validation = validate_large_trade_source(raw, source_file)
    master, master_read_report = read_master_readonly(master_parquet_path)
    previous_master_rows = int(len(master)) if master is not None else 0
    if master is None:
        blocking_errors = [
            *validation["blocking_errors"],
            f"master_read_blocked:{master_read_report.get('reason', 'unknown')}",
        ]
        report = base_report(
            status="blocked",
            reason=("validation_failed" if validation["blocking_errors"] else "master_read_blocked"),
            source_file=source_file,
            source_hash=source_hash,
            report_path=report_path,
            apply=apply,
            read_rows=int(len(raw)),
            previous_master_rows=previous_master_rows,
            candidate_new_rows=0,
            duplicate_rows=0,
            invalid_rows=int(validation["invalid_rows"]),
            final_expected_master_rows=previous_master_rows,
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
        report["master_read_report"] = master_read_report
        return report, None, None

    master = add_dedup_keys(master)
    incoming = add_dedup_keys(valid_incoming)
    missing_order_id_rows = int(incoming["_dedup_source"].eq("fingerprint").sum()) if len(incoming) else 0
    internal_duplicate_mask = incoming.duplicated(subset=["_dedup_key"], keep="last")
    duplicate_by_order_id_rows = int((internal_duplicate_mask & incoming["_dedup_source"].eq("order_id")).sum())
    duplicate_by_fingerprint_rows = int((internal_duplicate_mask & incoming["_dedup_source"].eq("fingerprint")).sum())
    incoming = incoming.drop_duplicates(subset=["_dedup_key"], keep="last")
    existing_keys = set(master["_dedup_key"].dropna().astype(str).tolist()) if len(master) else set()
    existing_duplicate_mask = incoming["_dedup_key"].astype(str).isin(existing_keys)
    duplicate_by_order_id_rows += int((existing_duplicate_mask & incoming["_dedup_source"].eq("order_id")).sum())
    duplicate_by_fingerprint_rows += int((existing_duplicate_mask & incoming["_dedup_source"].eq("fingerprint")).sum())
    new_rows = incoming.loc[~existing_duplicate_mask].copy()
    duplicate_rows = int(duplicate_by_order_id_rows + duplicate_by_fingerprint_rows)
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
        duplicate_by_order_id_rows=duplicate_by_order_id_rows,
        duplicate_by_fingerprint_rows=duplicate_by_fingerprint_rows,
        missing_order_id_rows=missing_order_id_rows,
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
    report["master_read_report"] = master_read_report
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
    duplicate_by_order_id_rows: int = 0,
    duplicate_by_fingerprint_rows: int = 0,
    missing_order_id_rows: int = 0,
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
        "duplicate_by_order_id_rows": int(duplicate_by_order_id_rows),
        "duplicate_by_fingerprint_rows": int(duplicate_by_fingerprint_rows),
        "missing_order_id_rows": int(missing_order_id_rows),
        "dedup_policy": DEDUP_POLICY,
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
        "apply_requested": bool(apply),
        "write_performed": False,
        "writes_trader_master": False,
        "writes_parquet": False,
        "writes_xlsx": False,
        "writes_csv": False,
        "writes_sqlite": False,
        "writes_runtime": False,
        "runtime_mode": "paper",
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "operational_authority": False,
        "created_at": utc_now(),
    }


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
    if apply:
        report = base_report(
            status="blocked",
            reason="legacy_master_apply_forbidden",
            source_file=source_file,
            source_hash=file_sha256(source_file) if source_file.exists() else None,
            report_path=report_path,
            apply=True,
            read_rows=0,
            previous_master_rows=0,
            candidate_new_rows=0,
            duplicate_rows=0,
            invalid_rows=0,
            final_expected_master_rows=0,
            blocking_errors=["legacy_master_apply_forbidden"],
            backup_dir=backup_dir,
            confirm_preflight_path=confirm_preflight_path,
            master_xlsx_path=master_xlsx_path,
            master_parquet_path=master_parquet_path,
            compatibility_xlsx_path=compatibility_xlsx_path,
        )
        write_report(report_path, report)
        return report
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
    del master, new_rows
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
