#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd


STAGING_AUDIT_SUMMARY = "PROJECT_STAGING_AUDIT_SUMMARY.json"
PREVIEW_SUMMARY = "BITRADEX_OCR_IMPORT_PREVIEW_SUMMARY.json"
IMPORT_READY_CSV = "BITRADEX_OCR_PHASE5_IMPORT_READY.csv"
IMPORT_READY_XLSX = "BITRADEX_OCR_PHASE5_IMPORT_READY.xlsx"
APPLY_SUMMARY = "APPLY_BITRADEX_OCR_ORDERID_SYNTHETIC_V5_SUMMARY.json"
POST_IMPORT_AUDIT = "POST_IMPORT_TRADES_MASTER_AUDIT_ORDERID_SYNTHETIC_V5.json"
ORDER_ID_RE = re.compile(r"^[0-9a-f]{24}$")
OFFICIAL_COLUMNS = [
    "moeda",
    "fechar_side",
    "leverage",
    "order_id",
    "pnl_fechado",
    "taxa_lucros_perdas_fechados_pct",
    "preco_abertura",
    "preco_fechamento",
    "volume_posicao",
    "volume_fechado",
    "horario_abertura",
    "horario_fechamento",
    "taxa_1",
    "preco_transacao",
    "volume_transacao",
    "direcao_liquidez",
    "taxa_2",
    "horario_transacao",
    "source_file",
    "imported_at",
    "_dedup_key",
    "_relaxed_dedup_key",
    "exchange_source",
    "market_data_source",
    "ocr_source",
]
SAFE_FALSE_FLAGS = (
    "live_trading_enabled",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "changes_risk",
    "changes_training_dataset",
    "writes_trades_master",
)


@dataclass(frozen=True)
class ApplyPaths:
    project_root: Path
    package_dir: Path
    master_xlsx: Path
    master_parquet: Path
    trades_excel_xlsx: Path
    backups_root: Path


def safety_payload(*, writes_official_trades_master: bool, backup_created: bool) -> dict[str, bool]:
    return {
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        "changes_training_dataset": False,
        "writes_trades_master": bool(writes_official_trades_master),
        "writes_official_trades_master": bool(writes_official_trades_master),
        "backup_created": bool(backup_created),
    }


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def timestamp_for_path() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_paths(args: argparse.Namespace) -> ApplyPaths:
    project_root = Path(args.project_root or ".").expanduser().resolve()
    return ApplyPaths(
        project_root=project_root,
        package_dir=Path(args.package_dir).expanduser().resolve(),
        master_xlsx=(project_root / "data" / "trades" / "trades_master.xlsx").resolve(),
        master_parquet=(project_root / "data" / "trades" / "trades_master.parquet").resolve(),
        trades_excel_xlsx=(project_root / "data" / "trades" / "trades_excel.xlsx").resolve(),
        backups_root=(project_root / "data" / "backups").resolve(),
    )


def find_excel_lock_files(paths: ApplyPaths) -> list[str]:
    locations = [paths.package_dir, paths.master_xlsx.parent]
    found: list[str] = []
    for location in locations:
        if not location.exists():
            continue
        found.extend(str(path) for path in sorted(location.glob("~$*.xlsx")))
    return found


def find_import_ready(package_dir: Path) -> Path | None:
    csv_path = package_dir / IMPORT_READY_CSV
    xlsx_path = package_dir / IMPORT_READY_XLSX
    if csv_path.exists():
        return csv_path
    if xlsx_path.exists():
        return xlsx_path
    return None


def read_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path, dtype=str, keep_default_na=False)
    if suffix == ".parquet":
        return pd.read_parquet(path)
    raise ValueError(f"unsupported_table_format:{path}")


def write_xlsx(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False)


def normalize_order_id(value: Any) -> str:
    text = "" if value is None else str(value).strip().lower()
    if text.endswith(".0"):
        text = text[:-2]
    return text


def normalize_symbol(value: Any) -> str:
    return str(value or "").strip().upper().replace("/", "").replace(":USDT", "")


def output_columns(master: pd.DataFrame, incoming: pd.DataFrame) -> list[str]:
    extras = [column for column in list(master.columns) + list(incoming.columns) if column not in OFFICIAL_COLUMNS]
    return OFFICIAL_COLUMNS + sorted(set(extras), key=extras.index)


def normalize_frame(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = frame.copy()
    out.columns = [str(column).strip() for column in out.columns]
    for column in columns:
        if column not in out.columns:
            out[column] = ""
    out = out[columns].copy()
    for column in out.columns:
        out[column] = out[column].fillna("").astype(str).str.strip()
    if "order_id" in out.columns:
        out["order_id"] = out["order_id"].map(normalize_order_id)
    if "moeda" in out.columns:
        out["moeda"] = out["moeda"].map(normalize_symbol)
    if "fechar_side" in out.columns:
        out["fechar_side"] = out["fechar_side"].str.lower()
    if "imported_at" in out.columns:
        empty = out["imported_at"].eq("")
        if empty.any():
            out.loc[empty, "imported_at"] = utc_timestamp()
    return out


def duplicate_rows(series: pd.Series) -> int:
    clean = series.fillna("").astype(str).map(normalize_order_id)
    clean = clean[clean.ne("")]
    return int(clean.duplicated(keep=False).sum())


def validate_gate_report(path: Path, *, kind: str) -> list[str]:
    if not path.exists():
        return [f"missing_{kind}:{path.name}"]
    try:
        payload = read_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"invalid_{kind}_json:{exc}"]
    errors: list[str] = []
    if payload.get("status") != "ok":
        errors.append(f"{kind}_status_not_ok:{payload.get('status')}")
    if int(payload.get("duplicate_internal_order_id_rows") or 0) != 0:
        errors.append(f"{kind}_duplicate_internal_order_id_rows_gt_0")
    if int(payload.get("duplicate_against_trades_master_rows") or 0) != 0:
        errors.append(f"{kind}_duplicate_against_trades_master_rows_gt_0")
    if payload.get("validation_errors") not in ([], None):
        errors.append(f"{kind}_validation_errors_present")
    if payload.get("writes_trades_master") is not False:
        errors.append(f"{kind}_writes_trades_master_not_false")
    if kind == "staging_audit" and int(payload.get("non_hex24_order_id_rows") or 0) != 0:
        errors.append("staging_audit_non_hex24_order_id_rows_gt_0")
    if kind == "preview":
        if payload.get("preview_only") is not True:
            errors.append("preview_only_not_true")
        if int(payload.get("problem_rows") or 0) != 0:
            errors.append("preview_problem_rows_gt_0")
    return errors


def validate_frames(incoming: pd.DataFrame, master: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, list[str], dict[str, int]]:
    columns = output_columns(master, incoming)
    incoming_norm = normalize_frame(incoming, columns)
    master_norm = normalize_frame(master, columns)
    errors: list[str] = []
    missing_official = [column for column in OFFICIAL_COLUMNS if column not in incoming.columns]
    if missing_official:
        errors.append("missing_official_columns:" + ",".join(missing_official))
    order_ids = incoming_norm["order_id"]
    non_hex = int((~order_ids.map(lambda value: bool(ORDER_ID_RE.fullmatch(value)))).sum())
    internal_dup = duplicate_rows(order_ids)
    master_ids = set(master_norm["order_id"].map(normalize_order_id))
    master_ids.discard("")
    duplicate_against_master = int(order_ids.isin(master_ids).sum())
    if non_hex:
        errors.append(f"non_hex24_order_id_rows:{non_hex}")
    if internal_dup:
        errors.append(f"duplicate_internal_order_id_rows:{internal_dup}")
    if duplicate_against_master and duplicate_against_master != len(incoming_norm):
        errors.append(f"duplicate_against_trades_master_rows:{duplicate_against_master}")
    counts = {
        "non_hex24_order_id_rows": non_hex,
        "duplicate_internal_order_id_rows": internal_dup,
        "duplicate_against_trades_master_rows": duplicate_against_master,
    }
    return incoming_norm, master_norm, errors, counts


def create_backup(paths: ApplyPaths) -> tuple[Path, dict[str, str | None]]:
    backup_dir = paths.backups_root / f"bitradex_ocr_v5_{timestamp_for_path()}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    copied = {
        "trades_master_xlsx": copy_if_exists(paths.master_xlsx, backup_dir / paths.master_xlsx.name),
        "trades_master_parquet": copy_if_exists(paths.master_parquet, backup_dir / paths.master_parquet.name),
        "trades_excel_xlsx": copy_if_exists(paths.trades_excel_xlsx, backup_dir / paths.trades_excel_xlsx.name),
    }
    return backup_dir, copied


def copy_if_exists(source: Path, destination: Path) -> str | None:
    if not source.exists():
        return None
    shutil.copy2(source, destination)
    return str(destination)


def rollback_command(paths: ApplyPaths, backup_dir: Path | None) -> str | None:
    if backup_dir is None:
        return None
    return f"Copy-Item -Force '{backup_dir / paths.master_xlsx.name}' '{paths.master_xlsx}'"


def base_summary(paths: ApplyPaths, import_ready: Path | None, *, no_write: bool, status: str, reason: str) -> dict[str, Any]:
    flags = safety_payload(writes_official_trades_master=False, backup_created=False)
    return {
        "status": status,
        "reason": reason,
        "created_at_utc": utc_timestamp(),
        "package_dir": str(paths.package_dir),
        "project_root": str(paths.project_root),
        "import_ready_path": str(import_ready) if import_ready else None,
        "import_ready_sha256": sha256_file(import_ready) if import_ready else None,
        "trades_master_path": str(paths.master_xlsx),
        "trades_master_sha256": sha256_file(paths.master_xlsx),
        "no_write": bool(no_write),
        "rows_before": 0,
        "incoming_rows": 0,
        "rows_after": 0,
        "imported_rows": 0,
        "backup_created": False,
        "backup_dir": None,
        "rollback_command": None,
        "writes_official_trades_master": False,
        "validation_errors": [],
        **flags,
    }


def post_audit_from_summary(summary: Mapping[str, Any], *, duplicate_after: int = 0, tail_match: bool = False) -> dict[str, Any]:
    flags = safety_payload(writes_official_trades_master=False, backup_created=False)
    return {
        "status": summary["status"],
        "reason": summary["reason"],
        "created_at_utc": utc_timestamp(),
        "rows_total": int(summary.get("rows_after") or summary.get("rows_before") or 0),
        "imported_rows": int(summary.get("imported_rows") or 0),
        "duplicate_order_id_rows_after": int(duplicate_after),
        "post_tail_source_match": bool(tail_match),
        "validation_errors": list(summary.get("validation_errors") or []),
        **flags,
    }


def finish(paths: ApplyPaths, summary: dict[str, Any], post_audit: dict[str, Any]) -> dict[str, Any]:
    write_json(paths.package_dir / APPLY_SUMMARY, summary)
    write_json(paths.package_dir / POST_IMPORT_AUDIT, post_audit)
    return summary


def apply_bitradex_ocr_orderid_synthetic_v5(paths: ApplyPaths, *, no_write: bool) -> dict[str, Any]:
    import_ready = find_import_ready(paths.package_dir)
    gate_errors: list[str] = []
    if not paths.package_dir.exists():
        gate_errors.append(f"missing_package_dir:{paths.package_dir}")
    if import_ready is None:
        gate_errors.append(f"missing_import_ready:{IMPORT_READY_CSV}_or_{IMPORT_READY_XLSX}")
    if not paths.master_xlsx.exists():
        gate_errors.append(f"missing_trades_master_xlsx:{paths.master_xlsx}")
    gate_errors.extend(validate_gate_report(paths.package_dir / STAGING_AUDIT_SUMMARY, kind="staging_audit"))
    gate_errors.extend(validate_gate_report(paths.package_dir / PREVIEW_SUMMARY, kind="preview"))
    lock_files = find_excel_lock_files(paths)
    if lock_files:
        gate_errors.append("excel_lock_files_present:" + ",".join(lock_files))
    if gate_errors:
        summary = base_summary(paths, import_ready, no_write=no_write, status="blocked", reason="gate_validation_failed")
        summary["validation_errors"] = sorted(set(gate_errors))
        return finish(paths, summary, post_audit_from_summary(summary))

    assert import_ready is not None
    incoming_raw = read_table(import_ready)
    master_raw = read_table(paths.master_xlsx)
    incoming, master, frame_errors, counts = validate_frames(incoming_raw, master_raw)
    rows_before = len(master)
    incoming_rows = len(incoming)
    all_duplicate_against_master = incoming_rows > 0 and counts["duplicate_against_trades_master_rows"] == incoming_rows
    if frame_errors and not all_duplicate_against_master:
        summary = base_summary(paths, import_ready, no_write=no_write, status="blocked", reason="frame_validation_failed")
        summary.update(
            {
                "rows_before": rows_before,
                "incoming_rows": incoming_rows,
                "rows_after": rows_before,
                "validation_errors": sorted(set(frame_errors)),
                **counts,
            }
        )
        return finish(paths, summary, post_audit_from_summary(summary, duplicate_after=duplicate_rows(master["order_id"])))
    if all_duplicate_against_master:
        summary = base_summary(paths, import_ready, no_write=no_write, status="idempotent_noop", reason="all_import_ready_rows_already_in_master")
        summary.update(
            {
                "rows_before": rows_before,
                "incoming_rows": incoming_rows,
                "rows_after": rows_before,
                "imported_rows": 0,
                **counts,
            }
        )
        return finish(paths, summary, post_audit_from_summary(summary, duplicate_after=duplicate_rows(master["order_id"])))
    if no_write:
        summary = base_summary(paths, import_ready, no_write=True, status="ok", reason="no_write_validation_ok")
        summary.update(
            {
                "rows_before": rows_before,
                "incoming_rows": incoming_rows,
                "rows_after": rows_before,
                "expected_rows_after": rows_before + incoming_rows,
                "imported_rows": 0,
                **counts,
            }
        )
        return finish(paths, summary, post_audit_from_summary(summary, duplicate_after=duplicate_rows(master["order_id"])))

    backup_dir, backup_files = create_backup(paths)
    combined = pd.concat([master, incoming], ignore_index=True, sort=False)
    duplicate_after = duplicate_rows(combined["order_id"])
    if duplicate_after:
        summary = base_summary(paths, import_ready, no_write=False, status="blocked", reason="post_concat_duplicate_order_ids")
        summary.update(
            {
                "rows_before": rows_before,
                "incoming_rows": incoming_rows,
                "rows_after": rows_before,
                "backup_created": True,
                "backup_dir": str(backup_dir),
                "backup_files": backup_files,
                "rollback_command": rollback_command(paths, backup_dir),
                "validation_errors": [f"duplicate_order_id_rows_after:{duplicate_after}"],
            }
        )
        return finish(paths, summary, post_audit_from_summary(summary, duplicate_after=duplicate_after))

    write_xlsx(paths.master_xlsx, combined)
    if paths.master_parquet.exists():
        combined.to_parquet(paths.master_parquet, index=False)
    if paths.trades_excel_xlsx.exists():
        write_xlsx(paths.trades_excel_xlsx, combined)
    post_master = normalize_frame(read_table(paths.master_xlsx), list(combined.columns))
    tail = post_master.tail(incoming_rows).reset_index(drop=True)
    expected_tail = incoming.reset_index(drop=True)
    post_tail_source_match = bool(tail.equals(expected_tail))
    post_errors: list[str] = []
    if len(post_master) != rows_before + incoming_rows:
        post_errors.append(f"rows_after_mismatch:expected={rows_before + incoming_rows},actual={len(post_master)}")
    if not post_tail_source_match:
        post_errors.append("post_tail_source_match_false")
    duplicate_after = duplicate_rows(post_master["order_id"])
    if duplicate_after:
        post_errors.append(f"duplicate_order_id_rows_after:{duplicate_after}")
    status = "ok" if not post_errors else "blocked"
    flags = safety_payload(writes_official_trades_master=True, backup_created=True)
    summary = base_summary(paths, import_ready, no_write=False, status=status, reason="official_apply_completed" if status == "ok" else "post_import_audit_failed")
    summary.update(
        {
            "rows_before": rows_before,
            "incoming_rows": incoming_rows,
            "rows_after": int(len(post_master)),
            "expected_rows_after": rows_before + incoming_rows,
            "imported_rows": incoming_rows if status == "ok" else 0,
            "backup_created": True,
            "backup_dir": str(backup_dir),
            "backup_files": backup_files,
            "rollback_command": rollback_command(paths, backup_dir),
            "writes_official_trades_master": True,
            "validation_errors": post_errors,
            **counts,
            **flags,
        }
    )
    post_audit = post_audit_from_summary(summary, duplicate_after=duplicate_after, tail_match=post_tail_source_match)
    post_audit["status"] = status
    post_audit["rows_total"] = int(len(post_master))
    post_audit["imported_rows"] = incoming_rows if status == "ok" else 0
    post_audit["validation_errors"] = post_errors
    return finish(paths, summary, post_audit)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply official Bitradex OCR v5 import-ready package to trades_master.")
    parser.add_argument("--package-dir", required=True)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    paths = resolve_paths(args)
    summary = apply_bitradex_ocr_orderid_synthetic_v5(paths, no_write=args.no_write)
    output = {
        "status": summary["status"],
        "reason": summary["reason"],
        "package_dir": summary["package_dir"],
        "project_root": summary["project_root"],
        "rows_before": summary["rows_before"],
        "incoming_rows": summary["incoming_rows"],
        "rows_after": summary["rows_after"],
        "imported_rows": summary["imported_rows"],
        "backup_created": summary["backup_created"],
        "writes_official_trades_master": summary["writes_official_trades_master"],
        "changes_training_dataset": False,
        "sends_orders": False,
        "changes_risk": False,
        "exchange_private_access": False,
        "validation_errors": summary.get("validation_errors", []),
    }
    if args.json:
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    else:
        print(f"{output['status']}: {output['reason']}")
    return 1 if summary["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
