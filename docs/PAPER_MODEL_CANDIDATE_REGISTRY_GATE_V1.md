# Paper Model Candidate Registry Gate V1

## Objetivo

Esta branch cria um gate institucional research-only/paper-only/shadow-only para consolidar candidatos de modelo, threshold, ensemble e regra vindos de Qlib, IA Shadow e relatórios de pesquisa.

O gate classifica candidatos e bloqueia qualquer promoção operacional. Ele não escreve registry ativo, não altera runtime, não altera modelos, não altera Freqtrade, não altera RiskManager e não envia ordens.

## Fontes Lidas

O CLI tenta ler, quando existirem:

- `data/reports/qlib_institutional_ranking_trainer_v1.json`
- `data/reports/ai_shadow_quality_veto_trainer_v1.json`
- `data/reports/qlib_shadow_ensemble_threshold_calibration_v1.json`
- `data/reports/ai_feature_source_fields_enrichment_contract_v1.json`
- `data/reports/financial_label_target_store_v1.json`
- `data/reports/ai_qlib_drift_regime_monitor_v1.json`
- `data/reports/event_driven_backtest_execution_cost_gate_v1.json`
- `data/reports/paper_autotrain_feedback_loop_v1.json`

Fontes ausentes ou inválidas são reportadas em `input_sources` com `exists=false` ou `load_error`, sem exceção não tratada.

## Candidatos Avaliados

O relatório materializa candidatos de:

- Qlib trainer
- IA Shadow quality veto trainer
- Qlib + IA Shadow ensemble threshold calibration
- Paper autotrain feedback loop

Cada candidato recebe:

- `candidate_id` determinístico
- `candidate_type`
- `source_id`
- `source_path`
- `source_sha256`
- escopos quando disponíveis
- threshold quando disponível
- resumo de métricas quando disponível
- `gate_status`
- `blocked_reasons`
- `eligible_for_research_review`
- `eligible_for_runtime=false`

## Estados Do Gate

O campo `registry_gate_status` pode retornar:

- `ok_research_review_only`
- `warning_review_required`
- `blocked_no_eligible_candidates`
- `blocked_missing_evidence`

Mesmo quando um candidato é elegível para revisão de pesquisa, ele permanece sem autoridade operacional.

## Regras De Bloqueio

O gate bloqueia candidatos quando:

- evidência do candidato está ausente;
- o drift/regime gate está bloqueado;
- o execution cost gate está bloqueado;
- o source fields contract não está pronto para `feature_notional` e `feature_quantity`;
- qualquer evidência tenta declarar autoridade de runtime, release, registry ou promoção;
- qualquer candidato dependeria de runtime, Freqtrade, RiskManager, Qlib runtime ou IA Shadow runtime.

## Research Registry Gate Versus Registry Ativo

Este componente não é o registry ativo de modelos. Ele é uma evidência de governança para revisão research-only.

Ele não:

- promove modelo;
- altera champion/challenger;
- escreve registry ativo;
- publica thresholds;
- atualiza IA Shadow;
- atualiza Qlib runtime;
- altera signal producer;
- altera Freqtrade ou RiskManager.

## Execução

No-write é o padrão:

```powershell
python .\scripts\build_paper_model_candidate_registry_gate_v1.py --project-root . --json
```

Com escrita explícita:

```powershell
python .\scripts\build_paper_model_candidate_registry_gate_v1.py --project-root . --write --json
```

Com `--write`, a CLI escreve somente:

- `data/reports/paper_model_candidate_registry_gate_v1.json`
- `data/reports/paper_model_candidate_registry_gate_v1.md`

Esses arquivos são runtime/evidence e não devem ser versionados.

## Safety Flags

Invariantes obrigatórios:

- `decision=MANTER_EM_RESEARCH`
- `research_only=true`
- `paper_only=true`
- `shadow_only=true`
- `read_only=true`
- `release_allowed=false`
- `live_release_allowed=false`
- `canary_release_allowed=false`
- `registry_write_performed=false`
- `candidate_registry_write_performed=false`
- `model_registry_write_performed=false`
- `runtime_registry_write_performed=false`
- `model_promotion_performed=false`
- `active_model_changed=false`
- `qlib_runtime_updated=false`
- `ai_shadow_runtime_updated=false`
- `updates_ai_shadow_thresholds=false`
- `updates_qlib_runtime=false`
- `updates_freqtrade=false`
- `updates_risk_manager=false`
- `changes_risk=false`
- `writes_runtime=false`
- `writes_registry=false`
- `writes_sqlite=false`
- `writes_parquet=false`
- `writes_model_artifact=false`
- `sends_orders=false`
- `exchange_private_access=false`

## Validação

Comandos esperados:

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest .\tests\test_paper_model_candidate_registry_gate_v1.py -q
python .\scripts\build_paper_model_candidate_registry_gate_v1.py --project-root . --json
python .\scripts\build_paper_model_candidate_registry_gate_v1.py --project-root . --write --json
python .\scripts\audit_state_execution_ledger_boundary.py --project-root . --json
python .\scripts\audit_operational_exception_swallowing.py --project-root . --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r .\requirements-dev.lock --progress-spinner off
python -m pip_audit -r .\requirements-qlib.lock --progress-spinner off
```

## Próximo Passo Esperado

A Branch 60 (`codex/paper-ai-signal-candidate-producer-v1`) deve consumir essa evidência como input research/paper-only. Ela não deve reinterpretar este gate como autorização de live, canary, registry ativo ou promoção.
