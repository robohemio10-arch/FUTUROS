# Bitradex OCR V1.1 Single Command Ingestion Orchestrator

## Objetivo

`scripts/run_bitradex_ocr_v11_single_command_ingestion.py` coordena, em modo paper/shadow, a descoberta do próximo lote Bitradex, o pipeline oficial de OCR/review, a validação do candidate revisado, o preview contra a master, o importador oficial, o sync dos sidecars e a Fase 5 opcional.

O comando reduz etapas manuais sem eliminar a revisão humana obrigatória. Ele não implementa OCR alternativo e não converte automaticamente texto OCR em trade oficial.

## Blocos Oficiais Descobertos

O orquestrador usa somente estes entrypoints existentes:

- OCR/review: `scripts/ocr_bitradex_images_to_review.py`;
- import com backup: `scripts/apply_bitradex_ocr_orderid_synthetic_v5_to_trades_master.py`;
- sync dos sidecars: `scripts/sync_ocr_master_v11_phase5_sidecars.py`;
- rebuild opcional: `scripts/rebuild_phase5_datasets.py`.

Não existe hoje um script oficial separado que transforme o review OCR em candidate import-ready. Portanto, o operador deve revisar e materializar `BITRADEX_OCR_PHASE5_IMPORT_READY.xlsx` ou `.csv` no package dir. Se esse arquivo não existir, o fluxo bloqueia com `missing_import_ready_candidate`.

## Dry-Run

Dry-run é o padrão. Ele pode escrever somente artefatos ignorados pelo Git no package dir e os relatórios runtime; nunca chama o importador oficial, nunca altera a master e nunca executa a Fase 5.

O lote esperado é de `50` imagens por padrão. Uma pasta com qualquer outra quantidade, inclusive o histórico completo, bloqueia antes do estágio OCR com `reason=input_image_count_mismatch`.

```powershell
python .\scripts\run_bitradex_ocr_v11_single_command_ingestion.py --dry-run --json
```

Com diretórios explícitos:

```powershell
python .\scripts\run_bitradex_ocr_v11_single_command_ingestion.py `
  --input-dir "E:\bitradex\Bitradex prints" `
  --package-dir ".\data\staging\bitradex_ocr_v11_next_lot" `
  --expected-image-count 50 `
  --dry-run `
  --json
```

Para uma auditoria deliberada de pasta com contagem diferente, use somente em dry-run:

```powershell
python .\scripts\run_bitradex_ocr_v11_single_command_ingestion.py `
  --input-dir "E:\bitradex\Bitradex prints" `
  --expected-image-count 50 `
  --allow-image-count-mismatch `
  --dry-run `
  --json
```

O relatório registra `image_count_mismatch_allowed=true` e um warning estruturado. O override é rejeitado em conjunto com `--apply-import`.

## Manifesto E Amostra Das Imagens

O stdout e o relatório principal nunca carregam o inventário completo. Eles contêm apenas:

- `input_image_count`;
- `expected_image_count`;
- `input_images_sample`;
- `input_images_sample_size`;
- `input_images_manifest_path`.

`--max-input-images-in-json` controla a amostra e usa `20` por padrão. O inventário completo, incluindo caminho relativo, tamanho e SHA-256, é gravado em:

```text
data/reports/bitradex_ocr_v11_input_images_manifest.json
```

O path pode ser substituído por `--input-images-manifest`. Esse JSON é runtime, permanece ignorado pelo Git e pode conter milhares de entradas sem poluir stdout.

O primeiro dry-run normalmente produz o review e bloqueia até que o candidate seja revisado. Depois da materialização do import-ready, execute o dry-run novamente para gerar:

- `PROJECT_STAGING_AUDIT_SUMMARY.json`;
- `BITRADEX_OCR_IMPORT_PREVIEW_SUMMARY.json`;
- `data/reports/bitradex_ocr_v11_single_command_ingestion_report.json`.

## Apply Import

O import exige flag explícita e worktree limpa:

```powershell
python .\scripts\run_bitradex_ocr_v11_single_command_ingestion.py `
  --input-dir "E:\bitradex\Bitradex prints" `
  --package-dir ".\data\staging\bitradex_ocr_v11_next_lot" `
  --expected-image-count 50 `
  --apply-import `
  --json
```

Antes de chamar o importador oficial, o orquestrador:

1. valida os hashes das imagens e o vínculo `source_file`/`imagem` com o lote atual;
2. compara o SHA-256 da master antes e depois do estágio OCR/review e bloqueia qualquer alteração;
3. valida schema, símbolos, lado, order ID, PnL, timestamps, preços e volumes;
4. bloqueia duplicidades internas ou contra a master;
5. gera preview com `rows_before`, `incoming_rows` e `expected_rows_after`;
6. cria backup próprio e verifica SHA-256 da cópia da master;
7. delega a escrita somente ao importador oficial, que cria seu próprio backup;
8. valida o post-import audit e as contagens;
9. chama o sync OCR V1.1 com hash e linhas observados após o import.

O relatório contém `backup_path`, `official_import_backup_path` e `rollback_command`. O rollback restaura a master a partir do backup anterior ao import.

## Contrato OCR V1.1

Quando a master contém o schema OCR V1.1, o import-ready deve preservar tanto as 25 colunas canônicas quanto as colunas-fonte usadas pelo sincronizador:

- `1_pnl_fechado` a `12_fechar_long_short`, conforme aplicável;
- `10_numero_do_pedido` e `11_moeda`;
- `fingerprint_operacional`.

Esse gate impede que uma linha seja anexada à master e depois não possa ser projetada nos sidecars da Fase 5. O orquestrador não inventa esses valores.

## Fase 5 Opcional

Fase 5 é opt-in e só é aceita junto com import oficial:

```powershell
python .\scripts\run_bitradex_ocr_v11_single_command_ingestion.py `
  --input-dir "E:\bitradex\Bitradex prints" `
  --package-dir ".\data\staging\bitradex_ocr_v11_next_lot" `
  --apply-import `
  --run-phase5 `
  --json
```

`--run-phase5` sem `--apply-import` retorna `blocked`. Quando executada, a Fase 5 registra as linhas de `trade_enriched` e `training_dataset`.

## Wrapper Windows

O wrapper apenas encaminha argumentos ao CLI Python:

```powershell
.\scripts\RUN_BITRADEX_OCR_V11_SINGLE_COMMAND_INGESTION.ps1 `
  -InputDir "E:\bitradex\Bitradex prints"
```

Para escrita, use `-ApplyImport`; para o rebuild posterior, adicione `-RunPhase5`.

## Critérios De Sucesso

- input contém imagens permitidas e ordenadas deterministicamente;
- `input_image_count` coincide com `expected_image_count` antes de OCR/import;
- scripts oficiais existem;
- candidate possui schema e proveniência válidos;
- nenhuma duplicidade interna ou contra a master;
- preview consistente;
- worktree limpa em apply;
- backups do orquestrador e do importador existem;
- post-import sem divergência de linhas ou order IDs;
- sync dos sidecars retorna `ok`;
- Fase 5 retorna `ok`, somente quando solicitada.

## Critérios De Bloqueio

- diretório ausente ou vazio;
- quantidade de imagens diferente da esperada sem override de auditoria;
- script oficial ausente;
- import-ready ausente;
- candidate sem campos críticos ou fonte V1.1;
- candidate não ligado às imagens do lote atual;
- order ID inválido ou duplicado;
- dirty worktree em apply;
- backup ausente;
- divergência pós-import;
- timeout ou falha de subprocesso.

## Segurança E Fora De Escopo

O fluxo preserva `paper_only=true`, `shadow_only=true` e todas as flags live/order/private exchange em `false`.

Ele não:

- promove quality-gated automaticamente;
- roda IA Shadow incremental;
- limpa SQLite IA Shadow;
- acessa exchange privada;
- envia ordens;
- altera risco ou modelo;
- habilita live ou canary;
- chama Freqtrade;
- versiona imagens, packages, backups ou relatórios runtime.

## Validação

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest .\tests\test_bitradex_ocr_v11_single_command_ingestion_orchestrator.py -q
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r .\requirements-dev.lock --progress-spinner off
```
