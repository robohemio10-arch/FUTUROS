# Paper AI/Qlib Autotrain Activation Closeout V1

## Objetivo

Esta branch consolida as evidências das Branches 59, 60 e 61 para fechar o
ciclo de ativação paper-only/research-only do caminho IA/Qlib/autotrain.

O objetivo é comprovar, de forma auditável, que a ativação operacional deve
permanecer bloqueada enquanto não existir caminho acionável:

```text
registry gate -> signal producer -> selector dry-run -> activation closeout
```

## Dependências

Fontes principais:

- `data/reports/paper_model_candidate_registry_gate_v1.json`
- `data/reports/paper_ai_signal_candidate_producer_v1.json`
- `data/reports/freqtrade_paper_ai_selector_e2e_dryrun_v1.json`

Fontes opcionais:

- `data/reports/paper_autotrain_feedback_loop_v1.json`
- `data/reports/qlib_institutional_ranking_trainer_v1.json`
- `data/reports/ai_shadow_quality_veto_trainer_v1.json`
- `data/reports/ai_qlib_drift_regime_monitor_v1.json`
- `data/reports/event_driven_backtest_execution_cost_gate_v1.json`
- `data/reports/monte_carlo_risk_ruin_stress_gate_v1.json`
- `data/reports/readiness_snapshot_v2.json`

Ausência de fonte principal bloqueia o closeout. Ausência de Monte Carlo ou
readiness também impede ativação, com diagnóstico conservador.

## Por que o estado atual bloqueia

O estado esperado das branches anteriores é:

- registry gate sem candidatos elegíveis;
- signal producer sem candidatos acionáveis;
- selector dry-run com zero sinais selecionados;
- drift/execution-cost ainda bloqueando o caminho.

Portanto o resultado esperado é:

```text
status=blocked
reason=blocked_no_actionable_ai_signal_path
decision=MANTER_EM_RESEARCH
activation_closeout_status=blocked_no_actionable_ai_signal_path
autotrain_operational_activation=false
paper_selector_runtime_enabled=false
sends_orders=false
```

## Closeout versus ativação operacional

Este closeout é evidência de pesquisa. Ele não registra scheduler, não inicia
serviço, não roda treino, não aplica threshold, não promove modelo e não conecta
o selector ao runtime paper.

Nenhuma saída desta branch deve ser consumida como sinal operacional.

## Escrita

O default da CLI é no-write. Com `--write`, a CLI escreve somente:

- `data/reports/paper_ai_qlib_autotrain_activation_closeout_v1.json`
- `data/reports/paper_ai_qlib_autotrain_activation_closeout_v1.md`

Esses arquivos são artefatos runtime ignorados pelo Git.

## Execução

```powershell
python .\scripts\build_paper_ai_qlib_autotrain_activation_closeout_v1.py --project-root . --json
python .\scripts\build_paper_ai_qlib_autotrain_activation_closeout_v1.py --project-root . --write --json
```

## Safety flags

A branch preserva:

- `live_trading_enabled=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `exchange_private_access=false`
- `sends_orders=false`
- `runs_training=false`
- `runs_autotrain=false`
- `scheduler_registered=false`
- `creates_cron=false`
- `creates_systemd_timer=false`
- `creates_windows_task=false`
- `creates_service=false`
- `starts_service=false`
- `qlib_runtime_updated=false`
- `ai_shadow_runtime_updated=false`
- `active_signal_file_written=false`
- `writes_active_freqtrade_signals=false`

## Validações

```powershell
python -m compileall -q scripts smartcrypto tests
python -m pytest .\tests\test_paper_ai_qlib_autotrain_activation_closeout_v1.py -q
python .\scripts\build_paper_ai_qlib_autotrain_activation_closeout_v1.py --project-root . --json
python .\scripts\build_paper_ai_qlib_autotrain_activation_closeout_v1.py --project-root . --write --json
python .\scripts\audit_state_execution_ledger_boundary.py --project-root . --json
python .\scripts\audit_operational_exception_swallowing.py --project-root . --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r .\requirements-dev.lock --progress-spinner off
python -m pip_audit -r .\requirements-qlib.lock --progress-spinner off
```

## Próximos passos

Antes de qualquer branch de runtime adapter, os blockers de registry, drift,
execution cost, Monte Carlo/readiness e go/no-go manual devem ser resolvidos em
evidências separadas. Esta branch não autoriza live, canary, paper runtime real,
ordens, alteração de risco, scheduler real, autotrain operacional ou promoção
de modelo.
