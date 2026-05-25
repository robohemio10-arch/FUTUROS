# PHASE5 — Importador incremental de trades OCR

## Decisão técnica

A planilha `trades_excel.xlsx` deixa de ser o ponto único manual e passa a ser um artefato de compatibilidade gerado a partir do histórico consolidado:

```text
data/trades/inbox/*.xlsx|csv|parquet
        ↓
Importador incremental
        ↓
data/trades/trades_master.xlsx
data/trades/trades_master.parquet
data/trades/trades_excel.xlsx
        ↓
Fase 3 rebuild
        ↓
trade_enriched.parquet
training_dataset.parquet
SQLite
```

## Deduplicação

A regra primária é `order_id`.

Quando `order_id` está ausente, o sistema usa fingerprint determinístico baseado em:

```text
moeda
fechar_side
horario_abertura
horario_fechamento
preco_abertura
preco_fechamento
pnl_fechado
```

Essas linhas são reportadas em `fingerprint_rows`, porque são menos confiáveis que deduplicação por `order_id`.

## Relatórios

A fase gera:

```text
data/reports/phase5_preflight_report.json
data/reports/phase5_import_report.json
data/reports/phase5_rebuild_report.json
data/reports/phase5_output_summary.json
data/reports/phase5_summary.json
```

## Evidência

A evidência final fica em:

```text
data/evidence/phase5_<timestamp>.zip
```

## Segurança operacional

Esta fase não executa ordens, não habilita live e não altera o Freqtrade. Ela apenas consolida dados e reconstrói datasets.
