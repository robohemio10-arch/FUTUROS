from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


CONTRACT: dict[str, Any] = json.loads("{\"status\": \"ok\", \"contract_version\": \"bitradex_ocr_canonical_v1\", \"project_root\": \"E:\\\\FUTUROS\", \"local_ocr_root\": \"E:\\\\bitradex\", \"trades_master_path\": \"E:\\\\FUTUROS\\\\data\\\\trades\\\\trades_master.xlsx\", \"trades_master_sheet\": \"Sheet1\", \"vital_project_paths\": {\"canonical_markdown\": \"E:\\\\FUTUROS\\\\docs\\\\VITAL_OCR_CANONICO_BITRADEX_v1.md\", \"canonical_text\": \"E:\\\\FUTUROS\\\\docs\\\\VITAL_OCR_CANONICO_BITRADEX_v1.txt\", \"canonical_python_contract\": \"E:\\\\FUTUROS\\\\scripts\\\\vital_bitradex_ocr_canonical_contract_v1.py\"}, \"reference_image\": {\"width\": 921, \"height\": 2048}, \"red_top_ignored\": true, \"ocr_policy\": \"black_rectangles_only\", \"black_rectangle_rois\": {\"moeda\": [25, 515, 610, 580], \"mercado_limite\": [28, 586, 172, 638], \"fechar_long_short\": [178, 586, 365, 638], \"preco_abertura\": [515, 808, 900, 875], \"preco_fechamento\": [515, 884, 900, 950], \"volume_posicao\": [515, 960, 900, 1028], \"volume_fechado\": [515, 1035, 900, 1105], \"horario_abertura\": [350, 1105, 910, 1185], \"horario_fechamento\": [350, 1180, 910, 1265], \"taxa_total\": [515, 1265, 900, 1340], \"numero_pedido\": [370, 1350, 900, 1425], \"preco_transacao\": [515, 1562, 900, 1638], \"volume_transacao\": [515, 1642, 900, 1713], \"direcao_liquidez\": [650, 1718, 900, 1788], \"taxa_execucao\": [515, 1795, 900, 1868], \"horario_transacao\": [350, 1870, 910, 1965]}, \"ocr_required_fields\": [\"moeda\", \"mercado_limite\", \"fechar_long_short\", \"preco_abertura\", \"preco_fechamento\", \"volume_posicao\", \"volume_fechado\", \"horario_abertura\", \"horario_fechamento\", \"taxa_total\", \"numero_pedido\", \"preco_transacao\", \"volume_transacao\", \"direcao_liquidez\", \"taxa_execucao\", \"horario_transacao\"], \"master_columns\": [\"moeda\", \"fechar_side\", \"leverage\", \"order_id\", \"pnl_fechado\", \"taxa_lucros_perdas_fechados_pct\", \"preco_abertura\", \"preco_fechamento\", \"volume_posicao\", \"volume_fechado\", \"horario_abertura\", \"horario_fechamento\", \"taxa_1\", \"preco_transacao\", \"volume_transacao\", \"direcao_liquidez\", \"taxa_2\", \"horario_transacao\", \"source_file\", \"imported_at\", \"_dedup_key\", \"_relaxed_dedup_key\", \"exchange_source\", \"market_data_source\", \"ocr_source\"], \"ocr_to_master_map\": {\"moeda\": \"moeda\", \"fechar_long_short\": \"fechar_side\", \"numero_pedido\": \"order_id\", \"pnl_fechado\": \"pnl_fechado\", \"preco_abertura\": \"preco_abertura\", \"preco_fechamento\": \"preco_fechamento\", \"volume_posicao\": \"volume_posicao\", \"volume_fechado\": \"volume_fechado\", \"horario_abertura\": \"horario_abertura\", \"horario_fechamento\": \"horario_fechamento\", \"taxa_total\": \"taxa_1\", \"preco_transacao\": \"preco_transacao\", \"volume_transacao\": \"volume_transacao\", \"direcao_liquidez\": \"direcao_liquidez\", \"taxa_execucao\": \"taxa_2\", \"horario_transacao\": \"horario_transacao\"}, \"price_guards\": {\"BTCUSDT\": [10000.0, 300000.0], \"ETHUSDT\": [500.0, 20000.0]}, \"ocr_states\": [\"RAW_OCR\", \"REVIEW_REQUIRED\", \"KEEP_FOR_STAGING_REVIEW\", \"MANUAL_REPAIR_REQUIRED\", \"EXCLUDE_OCR_STRUCTURAL_FAILURE\", \"EXCLUDE_DUPLICATE_ORDER_ID_KEEP_FIRST\", \"LOCKED_CANDIDATE_REQUIRES_MANUAL_APPROVAL\", \"PREVIEW_ONLY_NOT_IMPORTED\", \"IMPORTED_WITH_BACKUP\"], \"required_lot_files\": [\"bitradex_black_rectangles_ocr_review.xlsx\", \"bitradex_black_rectangles_fused_review.xlsx\", \"bitradex_fused_triage_review.xlsx\", \"bitradex_locked_staging_candidates_review.xlsx\", \"bitradex_import_canonical_locked_candidates.csv\", \"BITRADEX_OCR_IMPORT_PREVIEW_ONLY.xlsx\", \"BITRADEX_OCR_PHASE5_IMPORT_READY.xlsx\", \"APPLY_BITRADEX_OCR_IMPORT_SUMMARY.json\", \"POST_IMPORT_TRADES_MASTER_AUDIT_SUMMARY.json\"], \"safety_rules\": {\"ocr_full_screen_forbidden\": true, \"ignore_red_top_rectangle\": true, \"black_rectangle_rois_only\": true, \"forbid_empty_time_import\": true, \"forbid_duplicate_order_id_import\": true, \"require_preview_against_trades_master\": true, \"require_backup_before_master_write\": true, \"forbid_schema_incompatible_ai_dataset_overwrite\": true, \"sends_orders\": false, \"changes_risk\": false}, \"ai_shadow_feature_family_required\": [\"prior_*\", \"meta_*\", \"v13_*\"], \"ai_shadow_forbidden_direct_dataset_swap\": {\"from\": \"training_dataset.parquet\", \"to\": \"training_dataset_quality_gated_binance_1m.parquet\", \"reason\": \"Current shadow model expects prior_*, meta_* and v13_* features, not open_1m_*/close_1m_* schema.\"}}")


def emit_contract_json(output: str) -> dict[str, str]:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(CONTRACT, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
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
