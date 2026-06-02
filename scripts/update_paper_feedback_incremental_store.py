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


DEFAULT_INPUT = Path("data/trades/inbox/freqtrade_paper_closed_trades.csv")
DEFAULT_OUTPUT = Path("data/feedback/paper_closed_trades_incremental.parquet")
DEFAULT_REPORT = Path("data/reports/paper_feedback_incremental_store_report.json")
DEDUP_POLICY = "order_id_first_then_fingerprint"

REQUIRED_INPUT_COLUMNS = [
    "moeda",
    "fechar_side",
    "horario_abertura",
    "horario_fechamento",
    "preco_abertura",
    "preco_fechamento",
    "pnl_fechado",
    "taxa_lucros_perdas_fechados_pct",
]

STORE_COLUMNS = [
    "order_id",
    "moeda",
    "fechar_side",
    "horario_abertura",
    "horario_fechamento",
    "preco_abertura",
    "preco_fechamento",
    "pnl_fechado",
    "taxa_lucros_perdas_fechados_pct",
    "exit_reason",
    "source",
    "imported_at_utc",
    "record_hash",
]

FINGERPRINT_COLUMNS = [
    "moeda",
    "fechar_side",
    "horario_abertura",
    "horario_fechamento",
    "preco_abertura",
    "preco_fechamento",
    "pnl_fechado",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def stable_text(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def normalize_datetime_series(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, utc=True, errors="coerce")
    return parsed.dt.strftime("%Y-%m-%dT%H:%M:%SZ").astype("string")


def build_fingerprint_key(row: pd.Series) -> str:
    material = "|".join(stable_text(row.get(column, "")) for column in FINGERPRINT_COLUMNS)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"fingerprint::{digest}"


def build_dedup_identity(row: pd.Series) -> tuple[str, str, str]:
    normalized_order_id = normalize_order_id(row.get("order_id"))
    if normalized_order_id:
        return "order_id", f"order_id::{normalized_order_id}", normalized_order_id
    return "fingerprint", build_fingerprint_key(row), ""


def build_record_hash(row: pd.Series, dedup_key: str) -> str:
    payload = {column: stable_text(row.get(column, "")) for column in STORE_COLUMNS if column != "record_hash"}
    payload["dedup_key"] = dedup_key
    material = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


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


def read_input(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    raise ValueError(f"unsupported_input_extension:{path.suffix}")


def load_existing_store(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=STORE_COLUMNS)
    frame = pd.read_parquet(path)
    for column in STORE_COLUMNS:
        if column not in frame.columns:
            frame[column] = pd.NA
    return frame[STORE_COLUMNS].copy()


def validate_schema(frame: pd.DataFrame) -> list[str]:
    return [column for column in REQUIRED_INPUT_COLUMNS if column not in frame.columns]


def normalize_feedback_frame(frame: pd.DataFrame, *, source: str, imported_at_utc: str) -> pd.DataFrame:
    normalized = frame.copy()
    for column in ["order_id", "exit_reason"]:
        if column not in normalized.columns:
            normalized[column] = pd.NA
    for column in REQUIRED_INPUT_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = pd.NA

    normalized = normalized.dropna(how="all").copy()
    normalized["order_id"] = normalized["order_id"].map(normalize_order_id)
    normalized["horario_abertura"] = normalize_datetime_series(normalized["horario_abertura"])
    normalized["horario_fechamento"] = normalize_datetime_series(normalized["horario_fechamento"])
    normalized["source"] = source
    normalized["imported_at_utc"] = imported_at_utc

    for column in STORE_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = pd.NA
    for column in STORE_COLUMNS:
        if column not in {"record_hash"}:
            normalized[column] = normalized[column].astype("string").fillna("").str.strip()
    return normalized[STORE_COLUMNS].copy()


def add_dedup_columns(frame: pd.DataFrame) -> pd.DataFrame:
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


def finalize_new_rows(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    if len(result):
        result["record_hash"] = [
            build_record_hash(row, str(dedup_key))
            for (_, row), dedup_key in zip(result.iterrows(), result["_dedup_key"], strict=False)
        ]
    return result[STORE_COLUMNS].copy()


def blocked_report(
    *,
    reason: str,
    input_path: Path,
    output_path: Path,
    report_path: Path,
    input_rows: int = 0,
    existing_rows: int = 0,
    blocking_errors: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": reason,
        "input_path": str(input_path),
        "output_path": str(output_path),
        "report_path": str(report_path),
        "input_rows": int(input_rows),
        "existing_rows": int(existing_rows),
        "new_rows": 0,
        "duplicate_rows": 0,
        "final_rows": int(existing_rows),
        "duplicate_by_order_id_rows": 0,
        "duplicate_by_fingerprint_rows": 0,
        "missing_order_id_rows": 0,
        "dedup_policy": DEDUP_POLICY,
        "min_close_ts": None,
        "max_close_ts": None,
        "symbols": [],
        "sides": [],
        "blocking_errors": blocking_errors or [],
        "write_performed": False,
        "created_at": utc_now(),
        **safety_payload(),
    }


def update_incremental_store(
    *,
    input_path: Path = DEFAULT_INPUT,
    output_path: Path = DEFAULT_OUTPUT,
    report_path: Path = DEFAULT_REPORT,
    strict: bool = False,
) -> dict[str, Any]:
    existing = load_existing_store(output_path)
    existing_rows = int(len(existing))

    if not input_path.exists():
        report = blocked_report(
            reason="missing_input",
            input_path=input_path,
            output_path=output_path,
            report_path=report_path,
            existing_rows=existing_rows,
            blocking_errors=[f"input_missing:{input_path}"],
        )
        write_json(report_path, report)
        return report

    try:
        raw = read_input(input_path)
    except Exception as exc:
        report = blocked_report(
            reason="input_read_failed",
            input_path=input_path,
            output_path=output_path,
            report_path=report_path,
            existing_rows=existing_rows,
            blocking_errors=[repr(exc)],
        )
        write_json(report_path, report)
        return report

    missing_required = validate_schema(raw)
    if strict and missing_required:
        report = blocked_report(
            reason="invalid_schema",
            input_path=input_path,
            output_path=output_path,
            report_path=report_path,
            input_rows=int(len(raw)),
            existing_rows=existing_rows,
            blocking_errors=[f"missing_required_columns:{missing_required}"],
        )
        write_json(report_path, report)
        return report

    imported_at_utc = utc_now()
    incoming = normalize_feedback_frame(raw, source=str(input_path), imported_at_utc=imported_at_utc)
    incoming = add_dedup_columns(incoming)
    existing_with_keys = add_dedup_columns(existing)

    missing_order_id_rows = int(incoming["_dedup_source"].eq("fingerprint").sum()) if len(incoming) else 0
    internal_duplicate_mask = incoming.duplicated(subset=["_dedup_key"], keep="first")
    duplicate_by_order_id_rows = int((internal_duplicate_mask & incoming["_dedup_source"].eq("order_id")).sum())
    duplicate_by_fingerprint_rows = int((internal_duplicate_mask & incoming["_dedup_source"].eq("fingerprint")).sum())
    incoming_unique = incoming.loc[~internal_duplicate_mask].copy()

    existing_keys = set(existing_with_keys["_dedup_key"].dropna().astype(str).tolist()) if len(existing_with_keys) else set()
    existing_duplicate_mask = incoming_unique["_dedup_key"].astype(str).isin(existing_keys)
    duplicate_by_order_id_rows += int((existing_duplicate_mask & incoming_unique["_dedup_source"].eq("order_id")).sum())
    duplicate_by_fingerprint_rows += int((existing_duplicate_mask & incoming_unique["_dedup_source"].eq("fingerprint")).sum())

    new_rows_with_keys = incoming_unique.loc[~existing_duplicate_mask].copy()
    new_rows = finalize_new_rows(new_rows_with_keys)
    final = pd.concat([existing, new_rows], ignore_index=True, sort=False)
    final = final[STORE_COLUMNS].copy()

    ensure_parent(output_path)
    final.to_parquet(output_path, index=False)

    close_ts = pd.to_datetime(incoming["horario_fechamento"], utc=True, errors="coerce")
    report = {
        "status": "ok",
        "reason": "ok" if len(new_rows) else "no_new_rows",
        "input_path": str(input_path),
        "output_path": str(output_path),
        "report_path": str(report_path),
        "input_rows": int(len(raw)),
        "existing_rows": existing_rows,
        "new_rows": int(len(new_rows)),
        "duplicate_rows": int(duplicate_by_order_id_rows + duplicate_by_fingerprint_rows),
        "final_rows": int(len(final)),
        "duplicate_by_order_id_rows": int(duplicate_by_order_id_rows),
        "duplicate_by_fingerprint_rows": int(duplicate_by_fingerprint_rows),
        "missing_order_id_rows": int(missing_order_id_rows),
        "dedup_policy": DEDUP_POLICY,
        "min_close_ts": close_ts.dropna().min().isoformat() if not close_ts.dropna().empty else None,
        "max_close_ts": close_ts.dropna().max().isoformat() if not close_ts.dropna().empty else None,
        "symbols": sorted(incoming["moeda"].dropna().astype(str).loc[lambda value: value.ne("")].unique().tolist()),
        "sides": sorted(incoming["fechar_side"].dropna().astype(str).loc[lambda value: value.ne("")].unique().tolist()),
        "blocking_errors": [] if not missing_required else [f"missing_required_columns:{missing_required}"],
        "write_performed": True,
        "created_at": imported_at_utc,
        **safety_payload(),
    }
    write_json(report_path, report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Atualiza store incremental de feedback paper fechado.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--once", action="store_true", help="Execucao unica; default seguro.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = update_incremental_store(
        input_path=Path(args.input),
        output_path=Path(args.output),
        report_path=Path(args.report),
        strict=bool(args.strict),
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
