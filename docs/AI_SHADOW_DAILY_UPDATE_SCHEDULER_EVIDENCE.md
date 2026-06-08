# AI Shadow Daily Update Scheduler Evidence

Esta frente institucionaliza a evidencia do ciclo diario IA Shadow no host operacional. O processo atual e paper/shadow only e deve ser descrito como daily update de dados, score/log incremental e auditoria, nao como treinamento diario automatico do modelo.

## Escopo

O ciclo diario existente executa `scripts/RUN_DAILY_AI_SHADOW_UPDATE.ps1`. Esse script atualiza/audita dados, refaz quality gates, executa `scripts/run_ai_shadow_filter_incremental_daily.py`, audita o SQLite IA Shadow e gera `data/reports/daily_ai_shadow_update_summary.json`.

O modo permitido do ciclo e `score_and_log_only`. Nesse modo:

- `new_rows_scored=0` e valido quando nao ha trades novos.
- `inserted=0` e valido quando nao ha decisoes novas para registrar.
- Nenhuma ordem e enviada.
- Nenhum parametro de risco real e alterado.
- Nenhum modelo e promovido automaticamente.
- Nenhum registry de producao e alterado.

## Registro Da Tarefa Windows

Use o registrador versionado para criar ou reparar a tarefa agendada:

```powershell
powershell -ExecutionPolicy Bypass -File ".\scripts\register_ai_shadow_daily_update_task.ps1"
```

Parametros principais:

```powershell
powershell -ExecutionPolicy Bypass -File ".\scripts\register_ai_shadow_daily_update_task.ps1" `
  -ProjectRoot "E:\FUTUROS" `
  -TaskName "SmartCripto_AI_Daily_Update" `
  -DailyTime "00:00"
```

Para inspecionar sem registrar:

```powershell
powershell -ExecutionPolicy Bypass -File ".\scripts\register_ai_shadow_daily_update_task.ps1" -DryRun
```

A tarefa esperada usa:

- Execute: `powershell.exe`
- Arguments: `-NoProfile -ExecutionPolicy Bypass -File "E:\FUTUROS\scripts\RUN_DAILY_AI_SHADOW_UPDATE.ps1"`
- WorkingDirectory: `E:\FUTUROS`
- Trigger: diario

O caminho operacional antigo nao deve aparecer na action nem no working directory.

## Auditoria Do Agendamento

Gere evidencia local do scheduler com:

```powershell
python ".\scripts\audit_ai_shadow_daily_update_scheduler.py" `
  --project-root "E:\FUTUROS" `
  --task-name "SmartCripto_AI_Daily_Update" `
  --daily-summary ".\data\reports\daily_ai_shadow_update_summary.json" `
  --report ".\data\reports\ai_shadow_daily_update_scheduler_audit_report.json"
```

Em Windows, o auditor consulta o Agendador de Tarefas. Em Linux/CI, sem fixture, ele retorna `unsupported_platform` de forma controlada para manter os testes reproduziveis sem tocar no scheduler do host.

O relatorio valida:

- existencia da tarefa;
- action execute;
- action arguments;
- working directory;
- trigger diario;
- `LastTaskResult`;
- `last_run_time` e `next_run_time`;
- se aponta para o project root atual;
- se aponta para `RUN_DAILY_AI_SHADOW_UPDATE.ps1`;
- se ainda aponta para caminho antigo;
- safety flags paper/shadow only.

## Classificacao Semantica

O relatorio diferencia explicitamente:

- `daily_shadow_update_ok`: pipeline diario executou e o resumo esta coerente com `score_and_log_only`.
- `daily_training_not_performed`: nenhum retreino de modelo foi detectado.
- `daily_training_performed`: somente quando uma etapa real de treino gerar evidencia de modelo novo.
- `scheduler_broken`: tarefa existe mas aponta para caminho antigo ou tem `LastTaskResult` diferente de zero.
- `scheduler_configured`: tarefa aponta para `E:\FUTUROS` e para o script diario correto.

Essa distincao impede a afirmacao falsa de que existe treino diario quando o processo apenas pontua e registra decisoes IA Shadow.

## Seguranca

Esta frente nao libera live trading. Os relatorios e scripts preservam:

- `paper_only=true`
- `shadow_only=true`
- `live_trading_enabled=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `sends_orders=false`
- `exchange_private_access=false`
- `changes_risk=false`
- `model_promoted=false`

O auditor nao acessa exchange privada, nao chama Freqtrade DB, nao altera modelos, nao altera `trades_master` e nao escreve datasets versionados.

## Evidencia Runtime

Os relatorios gerados ficam em `data/reports/` e nao devem ser versionados:

- `data/reports/daily_ai_shadow_update_summary.json`
- `data/reports/ai_shadow_daily_update_scheduler_audit_report.json`

Validacao recomendada:

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests/test_ai_shadow_daily_update_scheduler_evidence.py -q
python -m pytest -q
python ".\scripts\generate_project_manifest.py" --check
python ".\scripts\scan_versioned_secrets.py" --json
```
