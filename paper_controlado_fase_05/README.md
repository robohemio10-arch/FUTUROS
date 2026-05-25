# Fase 5 — Importador incremental de trades OCR

## Objetivo

Importar novas planilhas/lotes OCR sem sobrescrever histórico, deduplicando por `order_id` e reconstruindo:

- `data/trades/trades_master.xlsx`
- `data/trades/trades_master.parquet`
- `data/trades/trades_excel.xlsx`
- `data/features/trade_enriched.parquet`
- `data/features/training_dataset.parquet`
- tabelas SQLite `trade_enriched` e `training_dataset`
- relatórios e evidências

## Como usar

Coloque novos arquivos OCR em:

```text
data/trades/inbox
```

Formatos aceitos:

```text
.xlsx
.xls
.csv
.parquet
```

Execute:

```powershell
.\paper_controlado_fase_05\RUN_PHASE5_PREFLIGHT.ps1
.\paper_controlado_fase_05\RUN_PHASE5_IMPORT_TRADES.ps1
.\paper_controlado_fase_05\RUN_PHASE5_REBUILD_DATASETS.ps1
.\paper_controlado_fase_05\RUN_PHASE5_VERIFY_OUTPUTS.ps1
.\paper_controlado_fase_05\RUN_PHASE5_COLLECT_EVIDENCE.ps1
```

Por padrão, arquivos importados são movidos para:

```text
data/trades/processed/<timestamp>
```

Para não arquivar automaticamente:

```powershell
.\paper_controlado_fase_05\RUN_PHASE5_IMPORT_TRADES.ps1 -NoArchive
```
