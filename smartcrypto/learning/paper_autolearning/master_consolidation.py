"""Paper feedback to trades master consolidation with explicit write gates."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from .feedback_store import (
    FIELD_CANDIDATES,
    clean_text,
    normalize_identity,
    normalize_side,
    normalize_symbol,
    normalize_time,
    safe_float,
)
from .outcome_schema import SAFETY_FLAGS, utc_now_iso

SCHEMA_VERSION = "paper_feedback_master_consolidation_v1"
DEFAULT_FEEDBACK_STORE = Path("data/feedback/paper_closed_trades_incremental.parquet")
DEFAULT_OUTCOME_EVENTS = Path("data/feedback/outcome_events.parquet")
DEFAULT_MICROBATCH_GLOB = Path("data/feedback/training_microbatches")
DEFAULT_INBOX_CSV = Path("data/trades/inbox/freqtrade_paper_closed_trades.csv")
DEFAULT_MASTER_XLSX = Path("data/trades/trades_master.xlsx")
DEFAULT_MASTER_PARQUET = Path("data/trades/trades_master.parquet")
DEFAULT_PREVIEW_JSON = Path("data/reports/paper_feedback_master_consolidation_preview_v1.json")
DEFAULT_PREVIEW_MD = Path("data/reports/paper_feedback_master_consolidation_preview_v1.md")
DEFAULT_BACKUP_ROOT = Path("data/backups/paper_feedback_master_consolidation")

MINIMAL_STAGING_COLUMNS = [
    "symbol",
    "symbol_norm",
    "side",
    "open_time_utc",
    "close_time_utc",
    "entry_price",
    "exit_price",
    "quantity",
    "net_pnl",
    "order_id",
    "internal_order_id",
    "trade_id",
    "row_fingerprint",
    "fingerprint_operacional",
    "dedup_source",
    "dedup_key",
    "source_file",
]

STAGING_FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
    **FIELD_CANDIDATES,
    "quantity": (
        "quantity",
        "amount",
        "qty",
        "contracts",
        "volume_posicao",
        "volume_fechado",
        "volume_transacao",
    ),
}

CONSOLIDATION_SAFETY_FLAGS: dict[str, bool] = {
    **SAFETY_FLAGS,
    "training_requested": False,
    "qlib_training_performed": False,
    "ai_shadow_training_performed": False,
    "model_promotion_performed": False,
    "active_model_changed": False,
    "registry_write_performed": False,
    "phase5_rebuild_requested": False,
    "phase5_rebuild_performed": False,
    "live_trading_enabled": False,
    "writes_runtime": False,
    "writes_sqlite": False,
}


@dataclass(frozen=True)
class SourceCandidate:
    source_id: str
    path: Path
    exists: bool
    rows: int
    status: str
    reason: str


def build_paper_feedback_master_consolidation_report(
    *,
    project_root: str | Path,
    source_path: str | Path | None = None,
    trades_master_xlsx_path: str | Path | None = None,
    trades_master_parquet_path: str | Path | None = None,
    preview_json_path: str | Path | None = None,
    preview_markdown_path: str | Path | None = None,
    backup_root: str | Path | None = None,
    write_preview: bool = False,
    write_master: bool = False,
) -> dict[str, Any]:
    """Build a preview report and optionally append validated rows to master."""

    root = Path(project_root).resolve()
    master_xlsx = _resolve(root, trades_master_xlsx_path, DEFAULT_MASTER_XLSX)
    master_parquet = _resolve(root, trades_master_parquet_path, DEFAULT_MASTER_PARQUET)
    preview_json = _resolve(root, preview_json_path, DEFAULT_PREVIEW_JSON)
    preview_md = _resolve(root, preview_markdown_path, DEFAULT_PREVIEW_MD)
    backups = _resolve(root, backup_root, DEFAULT_BACKUP_ROOT)

    input_sources, selected_path, source_rows = discover_input_source(root, source_path)
    master_frame, master_errors = read_master(master_xlsx, master_parquet)
    staging_frame, rejected_rows = build_staging(source_rows, selected_path)
    dedup = split_candidates(staging_frame, master_frame)
    rows_before = int(len(master_frame))
    rows_after_expected = int(rows_before + len(dedup["accepted"]))
    duplicate_after = duplicate_counts_after(master_frame, dedup["accepted"])

    validation_errors = validation_errors_for_preview(
        selected_path=selected_path,
        source_rows=source_rows,
        master_errors=master_errors,
        staging_frame=staging_frame,
        duplicate_after=duplicate_after,
    )
    staging_status = "ok" if not validation_errors else "blocked"

    backup_required = bool(write_master and len(dedup["accepted"]) > 0 and not validation_errors)
    backup_created = False
    backup_dir: str | None = None
    master_write_performed = False
    post_import_audit_status = "not_requested"
    status = "blocked" if validation_errors else "ok"
    reason = "preview_ready" if not validation_errors else validation_errors[0]

    if write_master:
        status, reason, backup_created, backup_dir, master_write_performed, post_import_audit_status = execute_master_write(
            master_frame=master_frame,
            accepted_frame=dedup["accepted"],
            master_xlsx=master_xlsx,
            master_parquet=master_parquet,
            backup_root=backups,
            validation_errors=validation_errors,
        )
        if master_write_performed:
            master_frame, master_errors = read_master(master_xlsx, master_parquet)
            rows_after_expected = int(len(master_frame))
            duplicate_after = duplicate_counts_after(master_frame, pd.DataFrame())
            if duplicate_after["duplicate_order_id_rows_after"] or duplicate_after["fingerprint_duplicate_rows_after"]:
                status = "blocked"
                reason = "post_import_duplicates_detected"
                post_import_audit_status = "blocked"

    report = build_report_payload(
        status=status,
        reason=reason,
        input_sources=input_sources,
        selected_input_source=selected_path,
        master_xlsx=master_xlsx,
        master_parquet=master_parquet,
        staging_status=staging_status,
        staging_rows=len(staging_frame),
        rows_before=rows_before,
        incoming_rows=len(staging_frame),
        accepted_rows=len(dedup["accepted"]),
        duplicate_rows=len(dedup["duplicates"]),
        rejected_rows=len(rejected_rows),
        rows_after=rows_after_expected,
        dedup=dedup,
        duplicate_after=duplicate_after,
        backup_required=backup_required,
        backup_created=backup_created,
        backup_dir=backup_dir,
        write_preview=write_preview,
        write_master=write_master,
        master_write_performed=master_write_performed,
        post_import_audit_status=post_import_audit_status,
        validation_errors=validation_errors if status != "blocked" or reason in validation_errors else [*validation_errors, reason],
    )

    preview_write_performed = False
    if write_preview:
        write_preview_outputs(report, preview_json, preview_md)
        preview_write_performed = True
    report["preview_write_performed"] = preview_write_performed
    report["write_performed"] = bool(preview_write_performed or master_write_performed)
    report["preview_json_path"] = str(preview_json)
    report["preview_markdown_path"] = str(preview_md)
    return report


def discover_input_source(root: Path, source_path: str | Path | None) -> tuple[list[dict[str, Any]], Path | None, list[dict[str, Any]]]:
    candidates: list[tuple[str, Path]] = []
    if source_path is not None:
        candidates.append(("explicit_source", _resolve(root, source_path, Path(source_path))))
    candidates.extend(
        [
            ("paper_closed_trades_incremental", root / DEFAULT_FEEDBACK_STORE),
            ("outcome_events", root / DEFAULT_OUTCOME_EVENTS),
            ("freqtrade_paper_closed_trades_csv", root / DEFAULT_INBOX_CSV),
        ]
    )
    microbatch_dir = root / DEFAULT_MICROBATCH_GLOB
    if microbatch_dir.exists():
        for path in sorted(microbatch_dir.glob("*.parquet"), reverse=True):
            candidates.insert(2, ("training_microbatch", path))

    reports: list[SourceCandidate] = []
    selected_path: Path | None = None
    selected_rows: list[dict[str, Any]] = []
    loaded_sources: list[tuple[Path, list[dict[str, Any]]]] = []
    seen: set[Path] = set()
    for source_id, path in candidates:
        if path in seen:
            continue
        seen.add(path)
        rows, status, reason = read_rows(path)
        reports.append(SourceCandidate(source_id, path, path.exists(), len(rows), status, reason))
        if rows:
            loaded_sources.append((path, rows))
        if source_path is not None and selected_path is None and rows:
            selected_path = path
            selected_rows = rows
    if source_path is None:
        for path, rows in loaded_sources:
            staged, _rejected = build_staging(rows, path)
            if not staged.empty:
                selected_path = path
                selected_rows = rows
                break
        if selected_path is None and loaded_sources:
            selected_path, selected_rows = loaded_sources[0]
    return [source_report(candidate) for candidate in reports], selected_path, selected_rows


def read_rows(path: Path) -> tuple[list[dict[str, Any]], str, str]:
    if not path.exists() or not path.is_file():
        return [], "missing", "source_missing"
    try:
        suffix = path.suffix.lower()
        if suffix == ".parquet":
            rows = pd.read_parquet(path).to_dict(orient="records")
        elif suffix == ".csv":
            rows = pd.read_csv(path).to_dict(orient="records")
        elif suffix == ".json":
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            rows = payload if isinstance(payload, list) else payload.get("rows", []) if isinstance(payload, Mapping) else []
        else:
            rows = []
        return [dict(row) for row in rows if isinstance(row, Mapping)], "ok", "source_loaded"
    except (OSError, ValueError, ImportError, json.JSONDecodeError) as exc:
        return [], "blocked", f"source_unreadable:{type(exc).__name__}"


def source_report(candidate: SourceCandidate) -> dict[str, Any]:
    return {
        "source_id": candidate.source_id,
        "path": str(candidate.path),
        "exists": candidate.exists,
        "rows": candidate.rows,
        "status": candidate.status,
        "reason": candidate.reason,
    }


def build_staging(rows: Sequence[Mapping[str, Any]], selected_path: Path | None) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    staged: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        normalized = normalize_trade(row, selected_path=selected_path, row_index=index)
        errors = validate_normalized_trade(normalized)
        if errors:
            rejected.append({"source_row_index": index, "validation_errors": errors, "fingerprint_operacional": normalized["fingerprint_operacional"]})
            continue
        staged.append(normalized)
    frame = pd.DataFrame(staged, columns=MINIMAL_STAGING_COLUMNS)
    if frame.empty:
        return frame, rejected
    frame["dedup_source"], frame["dedup_key"] = zip(*[dedup_identity(row) for row in frame.to_dict(orient="records")], strict=False)
    return frame, rejected


def normalize_trade(row: Mapping[str, Any], *, selected_path: Path | None, row_index: int) -> dict[str, Any]:
    mapped = {field: _first_value(row, candidates) for field, candidates in STAGING_FIELD_CANDIDATES.items()}
    symbol_raw = mapped["symbol"]
    symbol_norm = normalize_symbol(symbol_raw)
    side = normalize_side(mapped["side"])
    open_time = normalize_time(mapped["open_time_utc"])
    close_time = normalize_time(mapped["close_time_utc"])
    entry_price = safe_float(mapped["entry_price"])
    exit_price = safe_float(mapped["exit_price"])
    quantity = safe_float(mapped["quantity"])
    net_pnl = safe_float(mapped["net_pnl"])
    order_id = normalize_identity(mapped["order_id"])
    internal_order_id = normalize_identity(mapped["internal_order_id"])
    trade_id = normalize_identity(mapped["trade_id"])
    row_fingerprint = normalize_identity(row.get("row_fingerprint")) or normalize_identity(row.get("record_hash"))
    fingerprint = operational_fingerprint(
        symbol_norm=symbol_norm,
        side=side,
        open_time_utc=open_time,
        close_time_utc=close_time,
        entry_price=entry_price,
        exit_price=exit_price,
        quantity=quantity,
        net_pnl=net_pnl,
    )
    return {
        "symbol": clean_text(symbol_raw) or symbol_norm,
        "symbol_norm": symbol_norm,
        "side": side,
        "open_time_utc": open_time,
        "close_time_utc": close_time,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "quantity": quantity,
        "net_pnl": net_pnl,
        "order_id": order_id,
        "internal_order_id": internal_order_id,
        "trade_id": trade_id,
        "row_fingerprint": row_fingerprint,
        "fingerprint_operacional": fingerprint,
        "dedup_source": "",
        "dedup_key": "",
        "source_file": str(selected_path) if selected_path is not None else f"in_memory:{row_index}",
    }


def validate_normalized_trade(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ("symbol_norm", "side", "open_time_utc", "close_time_utc", "entry_price", "exit_price", "quantity", "net_pnl"):
        if row.get(key) in (None, ""):
            errors.append(f"missing_{key}")
    if row.get("close_time_utc") in (None, ""):
        errors.append("open_trade_rejected")
    if not row.get("order_id") and not row.get("fingerprint_operacional"):
        errors.append("missing_order_id_or_fingerprint_operacional")
    return sorted(set(errors))


def operational_fingerprint(**values: Any) -> str:
    material = json.dumps({key: _json_safe(value) for key, value in values.items()}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def dedup_identity(row: Mapping[str, Any]) -> tuple[str, str]:
    for source, key in (
        ("order_id", normalize_identity(row.get("order_id"))),
        ("internal_order_id", normalize_identity(row.get("internal_order_id"))),
        ("trade_id", normalize_identity(row.get("trade_id"))),
        ("row_fingerprint", normalize_identity(row.get("row_fingerprint"))),
        ("fingerprint_operacional", normalize_identity(row.get("fingerprint_operacional"))),
    ):
        if key:
            return source, f"{source}::{key}"
    return "missing", "missing::"


def split_candidates(staging: pd.DataFrame, master: pd.DataFrame) -> dict[str, pd.DataFrame | int]:
    staging = add_dedup_columns(staging)
    master = add_dedup_columns(normalize_master_frame(master))
    if staging.empty:
        empty = staging.copy()
        return {
            "accepted": empty,
            "duplicates": empty,
            "internal_duplicate_order_id_rows": 0,
            "internal_fingerprint_duplicate_rows": 0,
            "master_duplicate_order_id_rows": 0,
            "master_fingerprint_duplicate_rows": 0,
        }

    internal_duplicate_mask = staging.duplicated("dedup_key", keep="first")
    unique = staging.loc[~internal_duplicate_mask].copy()
    master_keys = set(master["dedup_key"].dropna().astype(str)) if not master.empty else set()
    master_duplicate_mask = unique["dedup_key"].astype(str).isin(master_keys)
    duplicates = pd.concat([staging.loc[internal_duplicate_mask], unique.loc[master_duplicate_mask]], ignore_index=True)
    accepted = unique.loc[~master_duplicate_mask].copy()
    internal_order = int((internal_duplicate_mask & staging["dedup_source"].eq("order_id")).sum())
    internal_fingerprint = int((internal_duplicate_mask & ~staging["dedup_source"].eq("order_id")).sum())
    master_order = int((master_duplicate_mask & unique["dedup_source"].eq("order_id")).sum())
    master_fingerprint = int((master_duplicate_mask & ~unique["dedup_source"].eq("order_id")).sum())
    return {
        "accepted": accepted,
        "duplicates": duplicates,
        "internal_duplicate_order_id_rows": internal_order,
        "internal_fingerprint_duplicate_rows": internal_fingerprint,
        "master_duplicate_order_id_rows": master_order,
        "master_fingerprint_duplicate_rows": master_fingerprint,
    }


def normalize_master_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    rows = [normalize_trade(row, selected_path=None, row_index=index) for index, row in enumerate(frame.to_dict(orient="records"), start=1)]
    return pd.DataFrame(rows, columns=MINIMAL_STAGING_COLUMNS)


def add_dedup_columns(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        result = frame.copy()
        for column in ("dedup_source", "dedup_key"):
            if column not in result:
                result[column] = pd.Series(dtype="string")
        return result
    result = frame.copy()
    identities = [dedup_identity(row) for row in result.to_dict(orient="records")]
    result["dedup_source"] = [item[0] for item in identities]
    result["dedup_key"] = [item[1] for item in identities]
    return result


def duplicate_counts_after(master: pd.DataFrame, accepted: pd.DataFrame) -> dict[str, int]:
    master_normalized = normalize_master_frame(master)
    master_keys = add_dedup_columns(master_normalized)[["dedup_source", "dedup_key"]] if not master_normalized.empty else pd.DataFrame()
    accepted_keys = add_dedup_columns(accepted)[["dedup_source", "dedup_key"]] if not accepted.empty else pd.DataFrame()
    key_rows = [frame for frame in (master_keys, accepted_keys) if not frame.empty]
    if not key_rows:
        return {"duplicate_order_id_rows_after": 0, "fingerprint_duplicate_rows_after": 0}
    final = pd.concat(key_rows, ignore_index=True)
    duplicate_mask = final.duplicated("dedup_key", keep=False)
    return {
        "duplicate_order_id_rows_after": int((duplicate_mask & final["dedup_source"].eq("order_id")).sum()),
        "fingerprint_duplicate_rows_after": int((duplicate_mask & ~final["dedup_source"].eq("order_id")).sum()),
    }


def read_master(master_xlsx: Path, master_parquet: Path) -> tuple[pd.DataFrame, list[str]]:
    if master_parquet.exists():
        try:
            return pd.read_parquet(master_parquet), []
        except (OSError, ValueError, ImportError) as exc:
            return pd.DataFrame(), [f"trades_master_parquet_unreadable:{type(exc).__name__}"]
    if master_xlsx.exists():
        try:
            return pd.read_excel(master_xlsx), []
        except (OSError, ValueError, ImportError) as exc:
            return pd.DataFrame(), [f"trades_master_xlsx_unreadable:{type(exc).__name__}"]
    return pd.DataFrame(), ["trades_master_missing"]


def validation_errors_for_preview(
    *,
    selected_path: Path | None,
    source_rows: Sequence[Mapping[str, Any]],
    master_errors: Sequence[str],
    staging_frame: pd.DataFrame,
    duplicate_after: Mapping[str, int],
) -> list[str]:
    errors: list[str] = []
    if selected_path is None:
        errors.append("input_source_empty")
    if not source_rows:
        errors.append("input_source_empty")
    errors.extend(master_errors)
    if staging_frame.empty and source_rows:
        errors.append("no_valid_staging_rows")
    if duplicate_after.get("duplicate_order_id_rows_after", 0) > 0:
        errors.append("duplicate_order_id_rows_after_gt_0")
    if duplicate_after.get("fingerprint_duplicate_rows_after", 0) > 0:
        errors.append("fingerprint_duplicate_rows_after_gt_0")
    return sorted(set(errors))


def execute_master_write(
    *,
    master_frame: pd.DataFrame,
    accepted_frame: pd.DataFrame,
    master_xlsx: Path,
    master_parquet: Path,
    backup_root: Path,
    validation_errors: Sequence[str],
) -> tuple[str, str, bool, str | None, bool, str]:
    if validation_errors:
        return "blocked", validation_errors[0], False, None, False, "blocked"
    if accepted_frame.empty:
        return "ok", "no_new_rows_to_append", False, None, False, "ok"
    try:
        backup_dir = create_master_backup(master_xlsx, master_parquet, backup_root)
    except OSError as exc:
        return "blocked", f"backup_failed:{type(exc).__name__}", False, None, False, "blocked"
    final = merge_for_master(master_frame, accepted_frame)
    master_xlsx.parent.mkdir(parents=True, exist_ok=True)
    final.to_excel(master_xlsx, index=False)
    if master_parquet.exists():
        final.to_parquet(master_parquet, index=False)
    return "ok", "master_append_completed", True, str(backup_dir), True, "ok"


def create_master_backup(master_xlsx: Path, master_parquet: Path, backup_root: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup_dir = backup_root / timestamp
    backup_dir.mkdir(parents=True, exist_ok=False)
    if master_xlsx.exists():
        shutil.copy2(master_xlsx, backup_dir / master_xlsx.name)
    if master_parquet.exists():
        shutil.copy2(master_parquet, backup_dir / master_parquet.name)
    return backup_dir


def merge_for_master(master: pd.DataFrame, accepted: pd.DataFrame) -> pd.DataFrame:
    accepted_public = accepted.drop(columns=[column for column in ("dedup_source", "dedup_key") if column in accepted], errors="ignore")
    final_columns = list(dict.fromkeys([*master.columns.tolist(), *accepted_public.columns.tolist()]))
    master_aligned = master.reindex(columns=final_columns)
    accepted_aligned = accepted_public.reindex(columns=final_columns)
    return pd.concat([master_aligned, accepted_aligned], ignore_index=True)


def build_report_payload(**kwargs: Any) -> dict[str, Any]:
    dedup = kwargs["dedup"]
    duplicate_after = kwargs["duplicate_after"]
    payload: dict[str, Any] = {
        "status": kwargs["status"],
        "reason": kwargs["reason"],
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_now_iso(),
        "input_sources": kwargs["input_sources"],
        "selected_input_source": str(kwargs["selected_input_source"]) if kwargs["selected_input_source"] is not None else None,
        "trades_master_xlsx_path": str(kwargs["master_xlsx"]),
        "trades_master_parquet_path": str(kwargs["master_parquet"]),
        "staging_status": kwargs["staging_status"],
        "staging_rows": int(kwargs["staging_rows"]),
        "rows_before": int(kwargs["rows_before"]),
        "incoming_rows": int(kwargs["incoming_rows"]),
        "accepted_rows": int(kwargs["accepted_rows"]),
        "duplicate_rows": int(kwargs["duplicate_rows"]),
        "rejected_rows": int(kwargs["rejected_rows"]),
        "rows_after": int(kwargs["rows_after"]),
        "internal_duplicate_order_id_rows": int(dedup["internal_duplicate_order_id_rows"]),
        "master_duplicate_order_id_rows": int(dedup["master_duplicate_order_id_rows"]),
        "duplicate_order_id_rows_after": int(duplicate_after["duplicate_order_id_rows_after"]),
        "internal_fingerprint_duplicate_rows": int(dedup["internal_fingerprint_duplicate_rows"]),
        "master_fingerprint_duplicate_rows": int(dedup["master_fingerprint_duplicate_rows"]),
        "fingerprint_duplicate_rows_after": int(duplicate_after["fingerprint_duplicate_rows_after"]),
        "backup_required": bool(kwargs["backup_required"]),
        "backup_created": bool(kwargs["backup_created"]),
        "backup_dir": kwargs["backup_dir"],
        "preview_write_requested": bool(kwargs["write_preview"]),
        "preview_write_performed": False,
        "master_write_requested": bool(kwargs["write_master"]),
        "master_write_performed": bool(kwargs["master_write_performed"]),
        "post_import_audit_status": kwargs["post_import_audit_status"],
        "phase5_rebuild_requested": False,
        "phase5_rebuild_performed": False,
        "training_requested": False,
        "qlib_training_performed": False,
        "ai_shadow_training_performed": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
        "registry_write_performed": False,
        **CONSOLIDATION_SAFETY_FLAGS,
        "safety_flags": dict(CONSOLIDATION_SAFETY_FLAGS),
        "validation_errors": sorted(set(kwargs["validation_errors"])),
    }
    return payload


def write_preview_outputs(report: Mapping[str, Any], json_path: Path, markdown_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")


def render_markdown(report: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# Paper Feedback Master Consolidation V1",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Selected input: `{report.get('selected_input_source')}`",
            f"- Rows before: `{report.get('rows_before')}`",
            f"- Incoming rows: `{report.get('incoming_rows')}`",
            f"- Accepted rows: `{report.get('accepted_rows')}`",
            f"- Duplicate rows: `{report.get('duplicate_rows')}`",
            f"- Rejected rows: `{report.get('rejected_rows')}`",
            f"- Rows after: `{report.get('rows_after')}`",
            f"- Master write performed: `{report.get('master_write_performed')}`",
            "",
            "This consolidation is paper/shadow only. It does not train models, write registries, promote models, send orders, change risk, access private exchange APIs, or touch SQLite.",
            "",
        ]
    )


def _first_value(row: Mapping[str, Any], candidates: Sequence[str]) -> object:
    lookup = {str(key).lower(): key for key in row}
    for candidate in candidates:
        key = lookup.get(candidate.lower())
        if key is not None:
            return row.get(key)
    return None


def _resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value
