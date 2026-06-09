# OCR Bitradex Official Apply Script Canonization

Esta etapa canoniza o script oficial do Bloco E para aplicar um pacote OCR Bitradex v5 já aprovado no `trades_master`.

Arquivo:

```text
scripts/apply_bitradex_ocr_orderid_synthetic_v5_to_trades_master.py
```

## Papel no Fluxo OCR

O Bloco E é o ponto de escrita oficial no master de trades. Ele não faz OCR, não faz revisão manual e não substitui o Bloco D. Ele apenas consome um pacote já preparado em:

```text
data/staging/bitradex_ocr/
```

ou em outro `--package-dir` informado explicitamente.

## Pré-Requisitos do Bloco D

Antes de qualquer escrita, o script exige:

- `PROJECT_STAGING_AUDIT_SUMMARY.json` com `status=ok`;
- `BITRADEX_OCR_IMPORT_PREVIEW_SUMMARY.json` com `status=ok` e `preview_only=true`;
- `BITRADEX_OCR_PHASE5_IMPORT_READY.csv` ou `BITRADEX_OCR_PHASE5_IMPORT_READY.xlsx`;
- `data/trades/trades_master.xlsx`;
- ausência de lock file Excel `~$*.xlsx` no pacote ou no diretório do master.

Os summaries de staging/preview devem declarar `writes_trades_master=false`, zero duplicidade interna, zero duplicidade contra master e lista vazia de `validation_errors`.

## Backup e Rollback

Em modo de escrita real, antes de alterar o master, o script cria:

```text
data/backups/bitradex_ocr_v5_<timestamp>/
```

Ele copia:

- `trades_master.xlsx`;
- `trades_master.parquet`, se existir;
- `trades_excel.xlsx`, se existir.

O summary contém `rollback_command` para restaurar o `trades_master.xlsx` a partir do backup.

## Aplicação Idempotente

O script valida `order_id` hex24, duplicidades internas e duplicidades contra o master. Se todo o lote já existir no master, a segunda execução retorna `idempotent_noop` e não duplica linhas.

Quando há linhas novas válidas:

- preserva as colunas oficiais primeiro;
- preserva colunas extras já existentes no master;
- anexa somente novas linhas;
- atualiza `trades_master.xlsx`;
- atualiza `trades_master.parquet` apenas se ele já existir;
- atualiza `trades_excel.xlsx` apenas se ele já existir.

## Summaries

O pacote recebe:

```text
APPLY_BITRADEX_OCR_ORDERID_SYNTHETIC_V5_SUMMARY.json
POST_IMPORT_TRADES_MASTER_AUDIT_ORDERID_SYNTHETIC_V5.json
```

O apply summary registra `rows_before`, `incoming_rows`, `rows_after`, `imported_rows`, `backup_created`, `backup_dir`, `rollback_command` e as flags de segurança.

O post-import audit registra `rows_total`, `imported_rows`, `duplicate_order_id_rows_after`, `post_tail_source_match` e `validation_errors`.

## Segurança

O script é paper/shadow only:

- não habilita live;
- não envia ordens;
- não acessa exchange privada;
- não altera risco;
- não altera modelos;
- não promove modelo;
- não altera `.env`;
- não altera `training_dataset`;
- não chama Freqtrade DB.

Os outputs sempre declaram `sends_orders=false`, `changes_risk=false`, `exchange_private_access=false` e `changes_training_dataset=false`.

## Uso

Dry-run institucional para validar gates sem escrever no master:

```powershell
python .\scripts\apply_bitradex_ocr_orderid_synthetic_v5_to_trades_master.py `
  --package-dir .\data\staging\bitradex_ocr `
  --project-root . `
  --no-write `
  --json
```

Aplicação oficial do Bloco E:

```powershell
python .\scripts\apply_bitradex_ocr_orderid_synthetic_v5_to_trades_master.py `
  --package-dir .\data\staging\bitradex_ocr `
  --project-root . `
  --json
```

## Relação com Fase 5 e Blocos F/G

O Bloco E só atualiza os arquivos oficiais de trades. Ele não reconstrói `trade_enriched`, `training_dataset` ou datasets derivados. Depois da aplicação, a sequência institucional continua pela Fase 5 ou pelos Blocos F/G do roadmap OCR, com rebuild e verificação próprios.

## Testes

Os testes usam `tmp_path` e fixtures locais. Eles não escrevem no `data/trades` real, não versionam artefatos runtime e não tocam em `trades_master` real.
