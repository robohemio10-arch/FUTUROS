# OCR Master V1.1 Phase5 Source Of Truth Alignment V1

## Objetivo

Este fluxo alinha os sidecars consumidos pela Fase 5 com a master OCR Candidate V1.1 oficial, sem modificar `data/trades/trades_master.xlsx`.

A master XLSX permanece a fonte de verdade imutável. `trades_excel.xlsx` e `trades_master.parquet` são projeções de compatibilidade regeneráveis com exatamente 25 colunas.

## Contrato De Entrada

O sincronizador valida antes de qualquer escrita:

- existência de `data/trades/trades_master.xlsx`;
- SHA-256 esperado;
- número esperado de linhas;
- colunas OCR obrigatórias;
- presença e unicidade de `fingerprint_operacional`.

Para a promoção OCR V1.1 atual, os valores institucionais são:

- linhas: `3058`;
- SHA-256: `83e2d17db317cc84b2bd39e00a961bd8d568c4375c5a4a113f6a26df58972e90`.

Qualquer divergência retorna `status=blocked`, não cria backup e não escreve sidecars.
O SHA-256 hexadecimal é comparado sem distinção entre maiúsculas e minúsculas; o relatório preserva exatamente o valor recebido em `master_sha256_expected`.

## Conversão Phase5

As colunas OCR `1_` a `12_` são mapeadas para o contrato canônico de trades. `fingerprint_operacional` alimenta `_dedup_key` e `_relaxed_dedup_key`. Campos transacionais não existentes ficam nulos, e `horario_transacao` usa `horario_fechamento` para preservar um timestamp compatível.

Metadados fixos:

- `exchange_source=bitradex`;
- `market_data_source=binance`;
- `ocr_source=bitradex_ocr_candidate_v1_1`.

O quality gate reconhece OCR por proveniência explícita: o marcador legado em `source_file` ou `ocr_source=bitradex_ocr_candidate_v1_1`. Valores reais de `source_file`, como o lote ou fila de origem, permanecem preservados e não são substituídos por um marcador artificial.

`order_id` é preservado como recebido, inclusive quando ausente.

## Identidade Institucional

`build_trade_enriched.py` seleciona `trade_id` nesta ordem:

1. `_dedup_key`, se todas as linhas estiverem preenchidas e forem únicas;
2. `order_id`, somente se todas estiverem preenchidas e forem únicas;
3. fingerprint determinístico do trade.

Se o fallback determinístico ainda produzir duplicatas, o build bloqueia. Duplicatas nunca são corrigidas por sufixo arbitrário.

## Dry-Run E Escrita

O modo `--no-write` lê e converte a master em memória, compara os sidecars e escreve somente o relatório de auditoria. Não cria backup nem altera XLSX/Parquet.

```powershell
python .\scripts\sync_ocr_master_v11_phase5_sidecars.py `
  --project-root . `
  --expected-master-sha256 83e2d17db317cc84b2bd39e00a961bd8d568c4375c5a4a113f6a26df58972e90 `
  --expected-rows 3058 `
  --no-write `
  --json
```

Sem `--no-write`, o sincronizador:

1. cria `data/backups/ocr_master_v11_phase5_alignment_YYYYMMDD_HHMMSS/`;
2. copia os sidecars existentes;
3. grava XLSX e Parquet temporários;
4. revalida linhas, colunas e `_dedup_key`;
5. substitui os sidecars;
6. confirma que o hash da master XLSX não mudou.

Quando os dois sidecars já correspondem à projeção canônica, a execução retorna `status=ok`, `reason=phase5_sidecars_already_aligned` e não cria backup nem regrava arquivos.

```powershell
python .\scripts\sync_ocr_master_v11_phase5_sidecars.py `
  --project-root . `
  --expected-master-sha256 83e2d17db317cc84b2bd39e00a961bd8d568c4375c5a4a113f6a26df58972e90 `
  --expected-rows 3058 `
  --json
```

O relatório fica em `data/reports/ocr_master_v11_phase5_source_alignment_report.json`.

## Gate Da Fase 5

`rebuild_phase5_datasets.py` bloqueia se master XLSX, compatibility XLSX ou master Parquet estiverem ausentes, ilegíveis ou com contagens divergentes. Quando o hash OCR V1.1 é detectado, a master deve ter 3058 linhas.

Depois do alinhamento:

```powershell
.\paper_controlado_fase_05\RUN_PHASE5_REBUILD_DATASETS.ps1
```

O quality-gated candidate pode ser reconstruído e auditado, mas não deve ser promovido se tiver menos linhas que o artefato oficial.

## Segurança

O sincronizador não acessa exchange, não envia ordens, não altera risco ou modelo, não modifica training dataset diretamente e nunca escreve `trades_master.xlsx`. Sidecars, backups e relatórios ficam sob `data/` e não são versionados.
