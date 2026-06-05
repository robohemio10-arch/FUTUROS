from __future__ import annotations

from dataclasses import dataclass


REQUIRED_COLUMNS = {
    "symbol": ["symbol", "moeda", "par", "pair", "ativo"],
    "open_time": ["horario_abertura", "data_abertura", "open_time", "entry_time", "abertura"],
    "close_time": ["horario_fechamento", "data_fechamento", "close_time", "exit_time", "fechamento"],
    "entry_price": ["preco_abertura", "entry_price", "preço_abertura", "preco_entrada", "entrada"],
    "exit_price": ["preco_fechamento", "exit_price", "preço_fechamento", "preco_saida", "saida"],
}

OPTIONAL_COLUMNS = {
    "close_side": ["fechar_side", "close_side", "side_fechamento", "lado_fechamento"],
    "side": ["side", "direcao", "posição", "posicao", "position_side"],
    "leverage": ["leverage", "alavancagem"],
    "order_id": ["order_id", "id_ordem", "ordem"],
    "pnl": ["pnl_fechado", "pnl", "lucro", "resultado"],
    "pnl_pct": ["taxa_lucros_perdas_fechados_pct", "pnl_pct", "resultado_pct", "taxa_pct"],
    "position_volume": ["volume_posicao", "position_volume", "volume_posição"],
    "closed_volume": ["volume_fechado", "closed_volume"],
    "fee_1": ["taxa_1"],
    "fee_2": ["taxa_2"],
}


@dataclass(frozen=True)
class ColumnMapping:
    canonical: str
    source: str
    required: bool


def normalize_column_name(name: object) -> str:
    text = str(name).strip().lower()
    text = text.replace("\n", " ").replace("\t", " ")
    text = " ".join(text.split())
    return (
        text.replace(" ", "_")
        .replace("-", "_")
        .replace(".", "_")
        .replace("%", "pct")
        .replace("ç", "c")
        .replace("ã", "a")
        .replace("á", "a")
        .replace("à", "a")
        .replace("é", "e")
        .replace("ê", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ô", "o")
        .replace("ú", "u")
    )


def build_column_mapping(columns: list[str]) -> list[ColumnMapping]:
    normalized_sources = {normalize_column_name(column): column for column in columns}
    mappings: list[ColumnMapping] = []

    for canonical, aliases in REQUIRED_COLUMNS.items():
        found = _find_alias(aliases, normalized_sources)
        if found:
            mappings.append(ColumnMapping(canonical, found, True))

    for canonical, aliases in OPTIONAL_COLUMNS.items():
        found = _find_alias(aliases, normalized_sources)
        if found:
            mappings.append(ColumnMapping(canonical, found, False))

    return mappings


def missing_required_columns(columns: list[str]) -> list[str]:
    normalized_sources = {normalize_column_name(column): column for column in columns}
    missing = []
    for canonical, aliases in REQUIRED_COLUMNS.items():
        if not _find_alias(aliases, normalized_sources):
            missing.append(canonical)
    return missing


def _find_alias(aliases: list[str], normalized_sources: dict[str, str]) -> str | None:
    for alias in aliases:
        normalized = normalize_column_name(alias)
        if normalized in normalized_sources:
            return normalized_sources[normalized]
    return None
