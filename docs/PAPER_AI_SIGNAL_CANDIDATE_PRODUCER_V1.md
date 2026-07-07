# Paper AI Signal Candidate Producer V1

## Objetivo

Esta branch cria um produtor research-only/paper-only/shadow-only de candidatos observacionais de sinal IA para paper.

O produtor consome o registry gate da Branch 59 e evidências existentes de Qlib, IA Shadow, ensemble threshold calibration, drift, execution cost e feedback. Ele não gera sinal operacional para Freqtrade, não escreve arquivo de sinal runtime, não altera RiskManager e não envia ordens.

## Dependência Da Branch 59

O input principal é:

- `data/reports/paper_model_candidate_registry_gate_v1.json`

O resultado real conhecido da Branch 59 é bloqueado:

- `registry_gate_status=blocked_no_eligible_candidates`
- `eligible_candidate_count=0`
- blockers: `blocked_drift_gate`, `blocked_execution_cost_gate`

Por isso, esta branch deve retornar `status=blocked` e `reason=no_registry_eligible_candidates` enquanto não houver candidatos elegíveis no registry gate.

## Fontes Lidas

O CLI tenta ler:

- `data/reports/paper_model_candidate_registry_gate_v1.json`
- `data/reports/qlib_shadow_ensemble_threshold_calibration_v1.json`
- `data/reports/qlib_institutional_ranking_trainer_v1.json`
- `data/reports/ai_shadow_quality_veto_trainer_v1.json`
- `data/reports/paper_autotrain_feedback_loop_v1.json`
- `data/reports/financial_label_target_store_v1.json`
- `data/reports/ai_qlib_drift_regime_monitor_v1.json`
- `data/reports/event_driven_backtest_execution_cost_gate_v1.json`

Fonte ausente ou inválida é reportada em `input_sources` e bloqueia a produção acionável.

## Candidato Observacional Versus Sinal Operacional

Um `signal_candidate` desta branch é apenas evidência de pesquisa. Ele não é um sinal operacional.

Todo candidato preserva:

- `eligible_for_paper_selector=false`
- `eligible_for_freqtrade=false`
- `operational_authority=false`
- `sends_orders=false`
- `writes_runtime=false`
- `updates_freqtrade=false`

Quando o registry gate está bloqueado ou sem elegíveis, os candidatos ficam com:

- `signal_actionability=blocked`

Mesmo em cenários de teste com registry elegível, o máximo permitido é:

- `signal_actionability=research_observation_only`

Isso não autoriza paper selector, Freqtrade, live, canary, ordem, registry ativo ou alteração de runtime.

## Anti-Leakage

O produtor não usa campos realizados como fonte de sinal, incluindo:

- `label`
- `target`
- `outcome`
- `pnl`
- `profit`
- `win_loss`
- `future_return`
- `expected_value`

Esses termos são removidos de resumos de métrica expostos em `ensemble_score_summary`.

## Execução

No-write é o padrão:

```powershell
python .\scripts\build_paper_ai_signal_candidate_producer_v1.py --project-root . --json
```

Com escrita explícita:

```powershell
python .\scripts\build_paper_ai_signal_candidate_producer_v1.py --project-root . --write --json
```

Com `--write`, a CLI escreve somente:

- `data/reports/paper_ai_signal_candidate_producer_v1.json`
- `data/reports/paper_ai_signal_candidate_producer_v1.md`

Esses arquivos são evidence/runtime local e não devem ser versionados.

## Safety Flags

Invariantes:

- `decision=MANTER_EM_RESEARCH`
- `research_only=true`
- `paper_only=true`
- `shadow_only=true`
- `read_only=true`
- `release_allowed=false`
- `signal_runtime_write_performed=false`
- `writes_signal_file=false`
- `writes_runtime=false`
- `writes_registry=false`
- `writes_sqlite=false`
- `writes_parquet=false`
- `writes_model_artifact=false`
- `model_promotion_performed=false`
- `registry_write_performed=false`
- `candidate_registry_write_performed=false`
- `qlib_runtime_updated=false`
- `ai_shadow_runtime_updated=false`
- `updates_ai_shadow_thresholds=false`
- `updates_qlib_runtime=false`
- `updates_freqtrade=false`
- `updates_risk_manager=false`
- `sends_orders=false`
- `exchange_private_access=false`

## Validação

Comandos esperados:

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest .\tests\test_paper_ai_signal_candidate_producer_v1.py -q
python .\scripts\build_paper_ai_signal_candidate_producer_v1.py --project-root . --json
python .\scripts\build_paper_ai_signal_candidate_producer_v1.py --project-root . --write --json
python .\scripts\audit_state_execution_ledger_boundary.py --project-root . --json
python .\scripts\audit_operational_exception_swallowing.py --project-root . --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r .\requirements-dev.lock --progress-spinner off
python -m pip_audit -r .\requirements-qlib.lock --progress-spinner off
```

## Próximo Passo Esperado

A Branch 61 (`codex/freqtrade-paper-ai-selector-e2e-dryrun-v1`) deve continuar tratando esta evidência como research/paper-only. Ela não deve converter candidatos bloqueados em sinais operacionais nem bypassar os gates da Branch 59.
