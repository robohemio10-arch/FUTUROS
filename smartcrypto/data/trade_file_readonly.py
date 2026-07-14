"""Read-only tabular trade-file normalization and validation helpers."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
    "fechar_side": {
        "fechar_side",
        "side",
        "lado",
        "direcao",
        "direção",
        "position_side",
        "close_side",
    },
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
    "preco_abertura": {
        "preco_abertura",
        "preço_abertura",
        "entry_price",
        "preco_entrada",
        "entrada",
    },
    "preco_fechamento": {
        "preco_fechamento",
        "preço_fechamento",
        "exit_price",
        "preco_saida",
        "saída",
        "saida",
    },
    "volume_posicao": {
        "volume_posicao",
        "volume_posição",
        "position_volume",
        "qtd_posicao",
    },
    "volume_fechado": {"volume_fechado", "closed_volume", "qtd_fechada"},
    "horario_abertura": {
        "horario_abertura",
        "horário_abertura",
        "open_time",
        "data_abertura",
        "entrada_ts",
    },
    "horario_fechamento": {
        "horario_fechamento",
        "horário_fechamento",
        "close_time",
        "data_fechamento",
        "saida_ts",
    },
    "taxa_1": {"taxa_1", "fee_1"},
    "preco_transacao": {
        "preco_transacao",
        "preço_transação",
        "transaction_price",
    },
    "volume_transacao": {"volume_transacao", "transaction_volume"},
    "direcao_liquidez": {
        "direcao_liquidez",
        "direção_liquidez",
        "liquidez",
        "maker_taker",
    },
    "taxa_2": {"taxa_2", "fee_2"},
    "horario_transacao": {
        "horario_transacao",
        "horário_transação",
        "transaction_time",
    },
}


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
    rename_map: dict[Any, str] = {}
    used: set[str] = set()
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


def clean_trade_frame(
    frame: pd.DataFrame,
    source_file: str | None = None,
) -> pd.DataFrame:
    normalized = normalize_columns(frame).dropna(how="all").copy()
    for column in CANONICAL_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = pd.NA
    normalized = normalized[CANONICAL_COLUMNS].copy()
    if source_file is not None:
        normalized["source_file"] = source_file
    normalized["imported_at"] = now_utc_iso()
    for column in normalized.columns:
        if column != "imported_at":
            normalized[column] = normalized[column].astype("string").str.strip()
    return normalized


def validate_trade_frame(frame: pd.DataFrame) -> dict[str, Any]:
    missing_required = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    missing_recommended = [
        column for column in RECOMMENDED_COLUMNS if column not in frame.columns
    ]
    empty_required: dict[str, int] = {}
    for column in REQUIRED_COLUMNS:
        if column in frame.columns:
            empty_required[column] = int(
                frame[column].isna().sum()
                + frame[column].astype("string").str.strip().eq("").sum()
            )
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
    material = "|".join(
        "" if pd.isna(value) else str(value).strip() for value in pieces
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"fingerprint::{digest}"


__all__ = [
    "CANONICAL_COLUMNS",
    "COLUMN_ALIASES",
    "RECOMMENDED_COLUMNS",
    "REQUIRED_COLUMNS",
    "SUPPORTED_EXTENSIONS",
    "build_alias_lookup",
    "build_dedup_key",
    "clean_trade_frame",
    "normalize_column_name",
    "normalize_columns",
    "now_utc_iso",
    "read_trade_file",
    "validate_trade_frame",
]
