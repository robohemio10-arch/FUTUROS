# Qlib Shadow Ensemble Threshold Calibration V1

## Objetivo

Esta camada cria uma calibração observacional research-only para combinar evidências de Qlib e IA Shadow em candidatos de threshold de ensemble. O resultado é uma sugestão descritiva para análise, sem autoridade operacional.

## Fontes

Fontes obrigatórias:

- `data/reports/financial_label_target_store_v1.json`
- `data/reports/paper_autotrain_feedback_loop_v1.json`
- `data/reports/ai_qlib_drift_regime_monitor_v1.json`
- `data/reports/event_driven_backtest_execution_cost_gate_v1.json`

Fontes opcionais:

- `data/reports/qlib_institutional_ranking_trainer_v1.json`
- `data/reports/ai_shadow_quality_veto_trainer_v1.json`

Se uma fonte obrigatória estiver ausente ou inválida, o relatório retorna `status=blocked`. Fontes opcionais ausentes geram warning controlado.

## Política De Score

O calibrador monta linhas em memória a partir do target store:

- `qlib_score`: usa campos de score Qlib quando presentes; caso contrário usa valor neutro `0.5`.
- `ai_shadow_score`: usa `probability_quality` por `order_id` quando disponível; caso contrário usa campos de score da própria linha ou valor neutro `0.5`.
- `ensemble_score`: média determinística entre `qlib_score` e `ai_shadow_score`.

O calibrador não usa o outcome financeiro como feature de decisão. O outcome entra apenas para medir o comportamento hipotético de cada threshold.

## Métricas Por Threshold

Cada candidato do grid expõe:

- `selected_count`
- `accepted_count`
- `rejected_count`
- `pnl_selected`
- `pnl_rejected`
- `precision_proxy`
- `recall_proxy`
- `average_expected_value`

O `recommended_candidate` é escolhido apenas para pesquisa. Ele não é aplicado no runtime, não altera IA Shadow, não altera Qlib e não escreve registry.

## Execução

No-write é o comportamento padrão:

```powershell
python .\scripts\build_qlib_shadow_ensemble_threshold_calibration_v1.py --project-root . --json
```

Para materializar evidência research-only em `data/reports`:

```powershell
python .\scripts\build_qlib_shadow_ensemble_threshold_calibration_v1.py --project-root . --write --json
```

Saídas com `--write`:

- `data/reports/qlib_shadow_ensemble_threshold_calibration_v1.json`
- `data/reports/qlib_shadow_ensemble_threshold_calibration_v1.md`

Esses arquivos são artefatos runtime e não devem ser versionados.

## Garantias De Segurança

Invariantes fixas:

- `decision=MANTER_EM_RESEARCH`
- `thresholds_applied=false`
- `release_allowed=false`
- `operational_authority=false`
- `updates_ai_shadow_thresholds=false`
- `updates_qlib_runtime=false`
- `writes_registry=false`
- `runs_training=false`
- `promotes_model=false`
- `changes_risk=false`
- `sends_orders=false`
- `exchange_private_access=false`
- `writes_runtime=false`
- `writes_sqlite=false`
- `writes_parquet=false`

Esta branch não promove modelo, não altera champion, não altera registry ativo, não altera Freqtrade, não altera RiskManager, não altera signal producer, não envia ordens e não acessa exchange privada.
