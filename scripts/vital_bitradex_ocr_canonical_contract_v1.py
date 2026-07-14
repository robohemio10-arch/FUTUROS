from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


CONTRACT: dict[str, Any] = {
    "status": "ok",
    "contract_version": "bitradex_ocr_canonical_v1",
    "project_root": "E:\\FUTUROS",
    "local_ocr_root": "E:\\bitradex",
    "legacy_dataset_integration": "disabled_by_research_only_governance",
    "reference_image": {"width": 921, "height": 2048},
    "red_top_ignored": True,
    "ocr_policy": "black_rectangles_only",
    "ocr_required_fields": [
        "moeda",
        "mercado_limite",
        "fechar_long_short",
        "preco_abertura",
        "preco_fechamento",
        "volume_posicao",
        "volume_fechado",
        "horario_abertura",
        "horario_fechamento",
        "taxa_total",
        "numero_pedido",
        "preco_transacao",
        "volume_transacao",
        "direcao_liquidez",
        "taxa_execucao",
        "horario_transacao",
    ],
    "price_guards": {
        "BTCUSDT": [10000.0, 300000.0],
        "ETHUSDT": [500.0, 20000.0],
    },
    "safety_rules": {
        "ocr_full_screen_forbidden": True,
        "ignore_red_top_rectangle": True,
        "black_rectangle_rois_only": True,
        "forbid_empty_time_import": True,
        "forbid_duplicate_order_id_import": True,
        "legacy_dataset_import_authorized": False,
        "legacy_dataset_write_authorized": False,
        "sends_orders": False,
        "changes_risk": False,
    },
}


def emit_contract_json(output: str) -> dict[str, str]:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(CONTRACT, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"status": "ok", "output": str(path)}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Contrato VITAL OCR Canonico Bitradex v1 para o projeto FUTUROS."
    )
    parser.add_argument("--emit-contract-json", type=str, default="")
    args = parser.parse_args()
    if args.emit_contract_json:
        print(json.dumps(emit_contract_json(args.emit_contract_json), indent=2, ensure_ascii=False))
        return 0
    print(json.dumps(CONTRACT, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
