# Paper Auto-learning Daily Scheduler V1

## Objetivo

Automatizar a cadencia logica diaria do foundation runner de autoaprendizado
paper/shadow, sem criar scheduler real nesta branch.

O comando orquestrado e:

```powershell
python .\scripts\run_paper_autolearning_foundation_v1.py --project-root . --write-feedback --train-smoke --json
```

Esta branch entrega apenas:

- dry-run da agenda;
- execucao unica com `--once`;
- passagem controlada de `--write-feedback`;
- passagem controlada de `--train-smoke`;
- bloqueio explicito de registro real de scheduler.

## Nao implementa

- cron real;
- systemd timer;
- Windows Task;
- servico Docker;
- docker-compose;
- loop infinito;
- alteracao do `trades_master`;
- model registry;
- promotion gate;
- triple-barrier;
- Qlib nativo completo;
- alteracao de champion;
- live/canary/orders.

## Modos

Dry-run padrao:

```powershell
python .\scripts\run_paper_autolearning_scheduler_v1.py --project-root . --json
```

Executar uma vez sem escrita:

```powershell
python .\scripts\run_paper_autolearning_scheduler_v1.py --project-root . --once --json
```

Executar uma vez com feedback e smoke advisory:

```powershell
python .\scripts\run_paper_autolearning_scheduler_v1.py --project-root . --once --write-feedback --train-smoke --json
```

Registro real continua bloqueado:

```powershell
python .\scripts\run_paper_autolearning_scheduler_v1.py --project-root . --register-scheduler --json
```

Resultado esperado:

- `scheduler_registration_status=blocked`
- `reason=scheduler_registration_deferred_to_deployment_branch`
- `scheduler_registration_performed=false`

## Campos principais

O JSON expõe:

- `scheduler_status`
- `scheduler_mode`
- `schedule_cadence`
- `next_planned_run_utc`
- `would_run_command`
- `executed_once`
- `foundation_runner_invoked`
- `closed_trades_loaded_count`
- `new_feedback_events_count`
- `duplicate_feedback_events_count`
- `microbatch_rows`
- `qlib_challenger_smoke_ran`
- `ai_shadow_challenger_smoke_ran`

## Garantias de seguranca

Sempre preserva:

- `paper_only=true`
- `shadow_only=true`
- `live_release_allowed=false`
- `canary_release_allowed=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `sends_orders=false`
- `exchange_private_access=false`
- `changes_risk=false`
- `writes_runtime=false`
- `writes_sqlite=false`
- `master_update_requested=false`
- `master_update_performed=false`
- `model_promotion_performed=false`
- `active_model_changed=false`
- `creates_cron=false`
- `creates_systemd_timer=false`
- `creates_windows_task=false`
- `creates_service=false`

## Proxima etapa

A criacao de cron, systemd, Windows Task ou servico Docker deve ficar em uma
branch de deployment separada, depois que este scheduler dry-run/once estiver
validado.
