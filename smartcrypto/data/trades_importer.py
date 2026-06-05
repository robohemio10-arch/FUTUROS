from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd


REQUIRED_COLUMNS = [
    "moeda",
    "fechar_side",
    "pnl_fechado",
    "preco_abertura",
    "preco_fechamento",
    "horario_abertura",
    "horario_fechamento",
]

RECOMMENDED_COLUMNS = [
    "leverage",
    "order_id",
    "taxa_lucros_perdas_fechados_pct",
    "volume_posicao",
    "volume_fechado",
    "taxa_1",
    "preco_transacao",
    "volume_transacao",
    "direcao_liquidez",
    "taxa_2",
    "horario_transacao",
]

CANONICAL_COLUMNS = [
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
]

SUPPORTED_EXTENSIONS = {".xlsx", ".xls", ".csv", ".parquet"}

COLUMN_ALIASES = {
    "moeda": {"moeda", "symbol", "simbolo", "símbolo", "par", "coin", "ativo"},
    "fechar_side": {"fechar_side", "side", "lado", "direcao", "direção", "position_side", "close_side"},
    "leverage": {"leverage", "alavancagem"},
    "order_id": {"order_id", "orderid", "id_ordem", "ordem", "id"},
    "pnl_fechado": {"pnl_fechado", "pnl", "lucro", "resultado", "realized_pnl"},
    "taxa_lucros_perdas_fechados_pct": {
        "taxa_lucros_perdas_fechados_pct",
        "pnl_pct",
        "retorno_pct",
        "taxa_pct",
        "resultado_pct",
    },
    "preco_abertura": {"preco_abertura", "preço_abertura", "entry_price", "preco_entrada", "entrada"},
    "preco_fechamento": {"preco_fechamento", "preço_fechamento", "exit_price", "preco_saida", "saída", "saida"},
    "volume_posicao": {"volume_posicao", "volume_posição", "position_volume", "qtd_posicao"},
    "volume_fechado": {"volume_fechado", "closed_volume", "qtd_fechada"},
    "horario_abertura": {"horario_abertura", "horário_abertura", "open_time", "data_abertura", "entrada_ts"},
    "horario_fechamento": {"horario_fechamento", "horário_fechamento", "close_time", "data_fechamento", "saida_ts"},
    "taxa_1": {"taxa_1", "fee_1"},
    "preco_transacao": {"preco_transacao", "preço_transação", "transaction_price"},
    "volume_transacao": {"volume_transacao", "transaction_volume"},
    "direcao_liquidez": {"direcao_liquidez", "direção_liquidez", "liquidez", "maker_taker"},
    "taxa_2": {"taxa_2", "fee_2"},
    "horario_transacao": {"horario_transacao", "horário_transação", "transaction_time"},
}


@dataclass(frozen=True)
class ImportFileResult:
    path: str
    status: str
    rows: int
    columns: list[str]
    error: str | None = None


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_column_name(value: object) -> str:
    text = str(value).strip().lower()
    text = text.replace("%", "pct")
    text = re.sub(r"[\s\-\/\.]+", "_", text)
    text = text.replace("__", "_")
    return text.strip("_")


def build_alias_lookup() -> dict[str, str]:
    lookup: dict[str, str] = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        lookup[normalize_column_name(canonical)] = canonical
        for alias in aliases:
            lookup[normalize_column_name(alias)] = canonical
    return lookup


def normalize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    lookup = build_alias_lookup()
    rename_map = {}
    used = set()

    for column in frame.columns:
        normalized = normalize_column_name(column)
        canonical = lookup.get(normalized, normalized)
        if canonical in used:
            suffix = 2
            candidate = f"{canonical}_{suffix}"
            while candidate in used:
                suffix += 1
                candidate = f"{canonical}_{suffix}"
            canonical = candidate
        used.add(canonical)
        rename_map[column] = canonical

    return frame.rename(columns=rename_map)


def read_trade_file(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()

    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    if suffix == ".parquet":
        return pd.read_parquet(path)

    raise ValueError(f"Extensão não suportada: {path.suffix}")


def clean_trade_frame(frame: pd.DataFrame, source_file: str | None = None) -> pd.DataFrame:
    normalized = normalize_columns(frame)
    normalized = normalized.dropna(how="all").copy()

    for column in CANONICAL_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = pd.NA

    normalized = normalized[CANONICAL_COLUMNS].copy()

    if source_file is not None:
        normalized["source_file"] = source_file

    normalized["imported_at"] = now_utc_iso()

    for column in normalized.columns:
        if column not in {"imported_at"}:
            normalized[column] = normalized[column].astype("string").str.strip()

    return normalized


def validate_trade_frame(frame: pd.DataFrame) -> dict:
    missing_required = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    missing_recommended = [column for column in RECOMMENDED_COLUMNS if column not in frame.columns]

    empty_required = {}
    for column in REQUIRED_COLUMNS:
        if column in frame.columns:
            empty_required[column] = int(frame[column].isna().sum() + frame[column].astype("string").str.strip().eq("").sum())

    return {
        "rows": int(len(frame)),
        "columns": list(frame.columns),
        "missing_required": missing_required,
        "missing_recommended": missing_recommended,
        "empty_required": empty_required,
    }


def build_dedup_key(row: pd.Series) -> str:
    order_id = row.get("order_id")
    order_id_text = "" if pd.isna(order_id) else str(order_id).strip()

    if order_id_text:
        return f"order_id::{order_id_text}"

    pieces = [
        row.get("moeda", ""),
        row.get("fechar_side", ""),
        row.get("horario_abertura", ""),
        row.get("horario_fechamento", ""),
        row.get("preco_abertura", ""),
        row.get("preco_fechamento", ""),
        row.get("pnl_fechado", ""),
    ]
    material = "|".join("" if pd.isna(value) else str(value).strip() for value in pieces)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"fingerprint::{digest}"


def list_inbox_files(inbox_dir: Path) -> list[Path]:
    if not inbox_dir.exists():
        return []

    return sorted(
        path
        for path in inbox_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS and not path.name.startswith("~$")
    )


def read_master(master_parquet_path: Path, master_xlsx_path: Path) -> pd.DataFrame:
    if master_parquet_path.exists():
        return pd.read_parquet(master_parquet_path)

    if master_xlsx_path.exists():
        return clean_trade_frame(pd.read_excel(master_xlsx_path), source_file=None)

    return pd.DataFrame(columns=CANONICAL_COLUMNS + ["source_file", "imported_at"])


def write_master(frame: pd.DataFrame, master_xlsx_path: Path, master_parquet_path: Path, compatibility_xlsx_path: Path) -> None:
    master_xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    master_parquet_path.parent.mkdir(parents=True, exist_ok=True)
    compatibility_xlsx_path.parent.mkdir(parents=True, exist_ok=True)

    output = frame.copy()
    output.to_parquet(master_parquet_path, index=False)
    output.to_excel(master_xlsx_path, index=False)
    output[CANONICAL_COLUMNS].to_excel(compatibility_xlsx_path, index=False)


def archive_files(files: Iterable[Path], processed_dir: Path, run_id: str) -> list[str]:
    archived: list[str] = []
    target_dir = processed_dir / run_id
    target_dir.mkdir(parents=True, exist_ok=True)

    for file in files:
        destination = target_dir / file.name
        if destination.exists():
            destination = target_dir / f"{file.stem}_{datetime.now().strftime('%H%M%S')}{file.suffix}"
        shutil.move(str(file), str(destination))
        archived.append(str(destination))

    return archived


def import_trades_incrementally(
    inbox_dir: Path,
    master_xlsx_path: Path,
    master_parquet_path: Path,
    compatibility_xlsx_path: Path,
    processed_dir: Path,
    report_path: Path,
    archive: bool = True,
) -> dict:
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    inbox_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)

    files = list_inbox_files(inbox_dir)

    master = read_master(master_parquet_path, master_xlsx_path)
    previous_rows = len(master)

    if not files:
        report = {
            "status": "no_input",
            "reason": "no_supported_files_in_inbox",
            "run_id": run_id,
            "inbox_dir": str(inbox_dir),
            "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
            "previous_master_rows": int(previous_rows),
            "final_master_rows": int(previous_rows),
            "new_rows": 0,
            "duplicate_rows": 0,
            "created_at": now_utc_iso(),
        }
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return report

    file_results: list[ImportFileResult] = []
    frames: list[pd.DataFrame] = []

    for file in files:
        try:
            raw = read_trade_file(file)
            cleaned = clean_trade_frame(raw, source_file=file.name)
            validation = validate_trade_frame(cleaned)

            if validation["missing_required"]:
                raise ValueError(f"Colunas obrigatórias ausentes: {validation['missing_required']}")

            frames.append(cleaned)
            file_results.append(
                ImportFileResult(
                    path=str(file),
                    status="ok",
                    rows=int(len(cleaned)),
                    columns=list(cleaned.columns),
                )
            )
        except Exception as exc:
            file_results.append(
                ImportFileResult(
                    path=str(file),
                    status="error",
                    rows=0,
                    columns=[],
                    error=str(exc),
                )
            )

    successful_frames = [frame for frame in frames if not frame.empty]
    read_rows = sum(len(frame) for frame in successful_frames)

    if successful_frames:
        incoming = pd.concat(successful_frames, ignore_index=True)
    else:
        incoming = pd.DataFrame(columns=CANONICAL_COLUMNS + ["source_file", "imported_at"])

    for frame in (master, incoming):
        if "_dedup_key" not in frame.columns:
            frame["_dedup_key"] = frame.apply(build_dedup_key, axis=1) if len(frame) else pd.Series(dtype="string")

    existing_keys = set(master["_dedup_key"].dropna().astype(str).tolist()) if len(master) else set()

    incoming["_dedup_key"] = incoming.apply(build_dedup_key, axis=1) if len(incoming) else pd.Series(dtype="string")
    incoming_before_internal_dedup = len(incoming)
    incoming = incoming.drop_duplicates(subset=["_dedup_key"], keep="last")
    internal_duplicates = incoming_before_internal_dedup - len(incoming)

    new_rows_frame = incoming.loc[~incoming["_dedup_key"].astype(str).isin(existing_keys)].copy()
    duplicate_existing_rows = len(incoming) - len(new_rows_frame)

    if len(new_rows_frame):
        combined = pd.concat([master, new_rows_frame], ignore_index=True)
    else:
        combined = master.copy()

    combined = combined.drop_duplicates(subset=["_dedup_key"], keep="last")
    combined = combined.sort_values(["horario_abertura", "order_id"], na_position="last").reset_index(drop=True)

    write_master(combined, master_xlsx_path, master_parquet_path, compatibility_xlsx_path)

    archived_files = archive_files(files, processed_dir, run_id) if archive else []

    report = {
        "status": "ok" if all(result.status == "ok" for result in file_results) else "partial",
        "run_id": run_id,
        "inbox_dir": str(inbox_dir),
        "input_files": [asdict(result) for result in file_results],
        "read_rows": int(read_rows),
        "previous_master_rows": int(previous_rows),
        "new_rows": int(len(new_rows_frame)),
        "duplicate_rows": int(internal_duplicates + duplicate_existing_rows),
        "internal_duplicate_rows": int(internal_duplicates),
        "duplicate_existing_rows": int(duplicate_existing_rows),
        "final_master_rows": int(len(combined)),
        "master_xlsx": str(master_xlsx_path),
        "master_parquet": str(master_parquet_path),
        "compatibility_xlsx": str(compatibility_xlsx_path),
        "archived_files": archived_files,
        "archive_enabled": bool(archive),
        "order_id_rows": int(combined["order_id"].fillna("").astype(str).str.strip().ne("").sum()) if "order_id" in combined.columns else 0,
        "fingerprint_rows": int(combined["_dedup_key"].astype(str).str.startswith("fingerprint::").sum()) if "_dedup_key" in combined.columns else 0,
        "created_at": now_utc_iso(),
    }

    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report
