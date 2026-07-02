# Paper Feedback Master Consolidation V1

## Objetivo

Esta branch cria uma consolidação auditável de trades paper/feedback para
staging e, somente com flag explícita, para `trades_master`.

O comportamento padrão é preview-only: o CLI lê fontes locais, normaliza trades,
deduplica por `order_id` primeiro, calcula fingerprint operacional e retorna
um relatório JSON sem alterar o master.

## Fontes lidas

Ordem padrão de descoberta:

1. `data/feedback/paper_closed_trades_incremental.parquet`
2. `data/feedback/outcome_events.parquet`
3. `data/feedback/training_microbatches/*.parquet`
4. `data/trades/inbox/freqtrade_paper_closed_trades.csv`
5. `data/trades/trades_master.xlsx`
6. `data/trades/trades_master.parquet`, se existir

As fontes são lidas localmente. O processo não acessa exchange, SQLite
operacional, Freqtrade API, registry, modelos ou Qlib runtime.

## Deduplicação

Política:

```text
order_id first
fallback: internal_order_id -> trade_id -> row_fingerprint -> fingerprint_operacional
```

O fingerprint operacional usa:

- `symbol_norm`
- `side`
- `open_time_utc`
- `close_time_utc`
- `entry_price`
- `exit_price`
- `quantity`
- `net_pnl`

## CLI

Preview default:

```powershell
python .\scripts\run_paper_feedback_master_consolidation_v1.py --project-root . --json
```

Escrever apenas relatório/Markdown de preview:

```powershell
python .\scripts\run_paper_feedback_master_consolidation_v1.py --project-root . --write-preview --json
```

Append oficial, apenas com autorização explícita do operador:

```powershell
python .\scripts\run_paper_feedback_master_consolidation_v1.py --project-root . --write-master --json
```

Não execute `--write-master` contra o master real durante validação padrão. Os
testes de escrita usam `tmp_path` e fixtures temporárias.

## Backup

Antes de qualquer escrita oficial, o consolidador cria backup timestampado sob:

```text
data/backups/paper_feedback_master_consolidation/
```

Se o backup falhar, `--write-master` bloqueia e não altera o master.

## Post-import audit

O relatório inclui:

- `rows_before`
- `incoming_rows`
- `accepted_rows`
- `duplicate_rows`
- `rejected_rows`
- `rows_after`
- `duplicate_order_id_rows_after`
- `fingerprint_duplicate_rows_after`
- `master_write_performed`
- `backup_created`

Escrita oficial só é permitida quando o staging está ok, não há erros de
validação e o audit final não apresenta duplicatas.

## Garantias de segurança

Sempre preservado:

- `paper_only=true`
- `shadow_only=true`
- `training_requested=false`
- `qlib_training_performed=false`
- `ai_shadow_training_performed=false`
- `model_promotion_performed=false`
- `active_model_changed=false`
- `registry_write_performed=false`
- `sends_orders=false`
- `exchange_private_access=false`
- `changes_risk=false`
- `writes_runtime=false`
- `writes_sqlite=false`

## Fora de escopo

Esta branch não altera:

- Freqtrade
- RiskManager
- signal producer
- Qlib runtime ativo
- IA Shadow runtime ativo
- registry
- champion model
- Docker/compose
- `.env`
- configs live
- SQLite operacional
- live/canary/order path

## Validação

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests\test_paper_feedback_master_consolidation_v1.py -q
python .\scripts\run_paper_feedback_master_consolidation_v1.py --project-root . --json
python .\scripts\run_paper_feedback_master_consolidation_v1.py --project-root . --write-preview --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r ".\requirements-dev.lock" --progress-spinner off
git diff --check
git diff --cached --check
git status --short
```
