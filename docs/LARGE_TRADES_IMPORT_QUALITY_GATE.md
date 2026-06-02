# Large Trades Import Quality Gate

## Objetivo

Este gate protege `trades_master.xlsx`, `trades_master.parquet` e os datasets
derivados antes da importacao de um lote grande de trades.

Ele e paper/shadow only: nao acessa exchange privada, nao envia ordens, nao
chama Freqtrade API, nao altera `.env` e nao treina modelo.

## Dry-Run Obrigatorio

Execute primeiro:

```powershell
python scripts/large_trades_import_quality_gate.py `
  --source-file data/trades/inbox/LOTE_GRANDE.xlsx
```

O comando gera:

```text
data/reports/large_trades_import_preflight_report.json
```

Campos principais:

- `status`
- `reason`
- `source_file`
- `read_rows`
- `candidate_new_rows`
- `duplicate_rows`
- `invalid_rows`
- `final_expected_master_rows`
- `min_trade_ts`
- `max_trade_ts`
- `symbols`
- `sides`
- `blocking_errors`
- `warnings`

`status=ok` significa que o arquivo pode ser aplicado. `reason=all_rows_duplicate`
tambem e seguro: o lote nao adicionaria linhas novas.

## Import Real Protegido

Somente depois de revisar um dry-run `ok`:

```powershell
python scripts/large_trades_import_quality_gate.py `
  --source-file data/trades/inbox/LOTE_GRANDE.xlsx `
  --apply
```

O import real valida que o relatorio dry-run salvo:

- esta `status=ok`;
- foi gerado em modo `dry_run`;
- aponta para o mesmo `source_file`;
- possui o mesmo hash SHA-256 da fonte;
- tem a mesma contagem esperada de linhas novas.

Antes de qualquer escrita, o gate cria backup em:

```text
data/backups/large_trades_import/<timestamp>/
```

## Depois Do Import

Reconstrua e valide os datasets pela Fase 5:

```powershell
.\paper_controlado_fase_05\RUN_PHASE5_REBUILD_DATASETS.ps1
.\paper_controlado_fase_05\RUN_PHASE5_VERIFY_OUTPUTS.ps1
```

## Validacoes

O preflight valida:

- schema e campos obrigatorios;
- datas parseaveis em UTC;
- `horario_fechamento >= horario_abertura`;
- simbolos permitidos: BTCUSDT/ETHUSDT;
- lado LONG/SHORT/BUY/SELL;
- PnL e precos numericos;
- duplicados internos e duplicados contra o master existente.

## Garantias

- dry-run nao altera `trades_master`;
- import real bloqueia se o preflight falhar;
- backup e obrigatorio antes de escrita;
- nenhum DB operacional do Freqtrade e tocado;
- nenhum arquivo runtime deve ser versionado.
