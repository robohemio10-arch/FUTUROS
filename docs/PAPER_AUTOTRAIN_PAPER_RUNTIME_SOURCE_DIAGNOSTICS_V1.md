# Paper Autotrain Paper Runtime Source Diagnostics V1

## Objetivo

`paper_autotrain_paper_runtime_source_diagnostics_v1` e um diagnostico research-only/read-only para explicar por que a esteira de autotrain paper nao recebeu novos trades fechados apos o watermark incremental.

Ele compara fontes locais paper/research:

- watermark incremental em `data/research/paper_autotrain_daily_quarantine_watermark/watermark_v1.json`;
- reports das Branches 66-69, quando existirem;
- exports de trades fechados em `data/trades/inbox/freqtrade_paper_closed_trades.csv`;
- eventos de feedback em `data/feedback/paper_autotrain_daily_quarantine_feedback_events_v1.jsonl`;
- microbatches em quarentena em `data/research/paper_autotrain_daily_quarantine/**/incremental_training_microbatch.parquet`;
- SQLite paper local resolvido em modo read-only, quando disponivel.

## Limites Operacionais

Esta branch somente diagnostica. Ela nao cria microbatch, nao treina, nao avalia candidato, nao promove modelo, nao escreve runtime, nao altera Freqtrade, nao altera RiskManager, nao altera Qlib/IA Shadow runtime e nao envia ordens.

Flags de seguranca esperadas:

- `research_only=true`
- `paper_only=true`
- `shadow_only=true`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `exchange_private_access=false`
- `sends_orders=false`
- `changes_risk=false`
- `writes_runtime=false`
- `writes_sqlite=false`
- `writes_parquet=false`
- `scheduler_registered=false`

## CLI

Default sem escrita:

```powershell
python .\scripts\build_paper_autotrain_paper_runtime_source_diagnostics_v1.py --project-root . --json
```

Com escrita explicita de report:

```powershell
python .\scripts\build_paper_autotrain_paper_runtime_source_diagnostics_v1.py --project-root . --write-report --json
```

Por seguranca, o SQLite paper nao e aberto por padrao. Para autorizar leitura read-only explicita:

```powershell
python .\scripts\build_paper_autotrain_paper_runtime_source_diagnostics_v1.py --project-root . --allow-paper-db-read --json
```

Com DB paper explicito:

```powershell
python .\scripts\build_paper_autotrain_paper_runtime_source_diagnostics_v1.py --project-root . --allow-paper-db-read --paper-db-path .\freqtrade\user_data\tradesv3.dryrun.sqlite --json
```

Saidas permitidas apenas com `--write-report`:

- `data/reports/paper_autotrain_paper_runtime_source_diagnostics_v1.json`
- `data/reports/paper_autotrain_paper_runtime_source_diagnostics_v1.md`

## Resolucao do SQLite Paper

A resolucao procura, nesta ordem:

1. `--paper-db-path`;
2. `user_data/tradesv3.sqlite`;
3. `user_data/tradesv3.dryrun.sqlite`;
4. `freqtrade/user_data/tradesv3.sqlite`;
5. `freqtrade/user_data/tradesv3.dryrun.sqlite`;
6. `data/runtime/freqtrade/tradesv3.sqlite`;
7. `data/runtime/freqtrade/tradesv3.dryrun.sqlite`;
8. `data/freqtrade/tradesv3.sqlite`;
9. `data/freqtrade/tradesv3.dryrun.sqlite`.

Quando `--allow-paper-db-read` nao e usado, o report retorna `paper_db_read_requested=false` e `paper_db_status=not_requested`, sem tentar abrir SQLite. Quando a flag e usada e o arquivo existe, a leitura usa URI SQLite read-only no formato `file:<path>?mode=ro`. Falha de abertura ou schema invalido retorna status controlado, sem traceback cru.

## Diagnosticos

O report pode classificar:

- `no_new_closed_paper_trades_after_watermark`: nenhuma fonte local tem registro novo depois do watermark;
- `paper_db_new_trades_not_exported`: o paper DB tem registros novos, mas exports/feedback/microbatch nao tem;
- `exports_feedback_new_trades_not_microbatched`: CSV ou feedback tem registros novos, mas microbatch nao tem;
- `paper_source_divergence_detected`: as fontes divergem entre si;
- `paper_db_source_missing_or_unreadable`: fonte autoritativa SQLite paper ausente, ilegivel ou com schema invalido;
- `indeterminate_paper_runtime_source_state`: fontes insuficientes ou estado nao conclusivo.

Todas as decisoes permanecem fail-closed. Nenhuma decisao operacional e liberada por este diagnostico.

## Validacao

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest .\tests\test_paper_autotrain_paper_runtime_source_diagnostics_v1.py -q
python .\scripts\build_paper_autotrain_paper_runtime_source_diagnostics_v1.py --project-root . --json
python .\scripts\build_paper_autotrain_paper_runtime_source_diagnostics_v1.py --project-root . --write-report --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
```
