from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import pandas as pd


TRADES_PATH = Path("data/trades/trades_excel.xlsx")
REPORT_PATH = Path("data/reports/trades_excel_validation.json")

REQUIRED_COLUMNS = [
    "moeda",
    "horario_abertura",
    "horario_fechamento",
    "preco_abertura",
    "preco_fechamento",
]

RECOMMENDED_COLUMNS = [
    "fechar_side",
    "leverage",
    "order_id",
    "pnl_fechado",
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


def normalize_column(name: str) -> str:
    raw = str(name).strip().lower()
    raw = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    raw = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    aliases = {
        "symbol": "moeda",
        "par": "moeda",
        "ativo": "moeda",
        "coin": "moeda",
        "side": "fechar_side",
        "lado": "fechar_side",
        "preco_entrada": "preco_abertura",
        "entry_price": "preco_abertura",
        "preco_saida": "preco_fechamento",
        "exit_price": "preco_fechamento",
        "data_abertura": "horario_abertura",
        "opening_time": "horario_abertura",
        "entry_time": "horario_abertura",
        "data_fechamento": "horario_fechamento",
        "closing_time": "horario_fechamento",
        "exit_time": "horario_fechamento",
        "pnl": "pnl_fechado",
        "lucro": "pnl_fechado",
        "lucro_prejuizo": "pnl_fechado",
    }
    return aliases.get(raw, raw)


def main() -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not TRADES_PATH.exists():
        result = {
            "status": "missing_file",
            "path": str(TRADES_PATH),
            "message": "Coloque a planilha OCR em data/trades/trades_excel.xlsx",
        }
        REPORT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(1)

    frame = pd.read_excel(TRADES_PATH)
    normalized_columns = [normalize_column(col) for col in frame.columns]
    frame.columns = normalized_columns

    missing_required = [col for col in REQUIRED_COLUMNS if col not in frame.columns]
    missing_recommended = [col for col in RECOMMENDED_COLUMNS if col not in frame.columns]

    result = {
        "status": "ok" if not missing_required else "error",
        "path": str(TRADES_PATH),
        "rows": int(len(frame)),
        "columns": list(frame.columns),
        "missing_required": missing_required,
        "missing_recommended": missing_recommended,
        "empty_rows": int(frame.dropna(how="all").shape[0] != len(frame)),
    }

    REPORT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if missing_required:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
