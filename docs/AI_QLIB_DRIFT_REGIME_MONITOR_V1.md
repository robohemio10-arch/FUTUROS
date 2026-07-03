# AI/Qlib Drift Regime Monitor V1

## Objetivo

O `ai_qlib_drift_regime_monitor_v1` cria uma evidência institucional de drift e regime para os artefatos de IA/Qlib do SMART FUTUROS.

Ele é **research-only/read-only por padrão**. O monitor não treina modelo, não promove challenger, não escreve registry ativo, não atualiza runtime, não altera Freqtrade/RiskManager, não envia ordens e não acessa exchange privada.

## Fontes de Verdade

O monitor lê relatórios JSON já existentes:

- `data/reports/ai_unified_feature_contract_v1.json`
- `data/reports/ai_unified_dataset_manifest_v1.json`
- `data/reports/financial_label_target_store_v1.json`
- `data/reports/walkforward_anti_leakage_split_engine_v1.json`
- `data/reports/walkforward_baseline_summary_v1.json`
- `data/reports/qlib_institutional_ranking_trainer_v1.json`
- `data/reports/ai_shadow_quality_veto_trainer_v1.json`
- `data/reports/paper_autotrain_feedback_loop_v1.json`
- `data/reports/daily_learning_evidence_readiness_integration_v1.json`

As quatro primeiras fontes são obrigatórias para análise mínima de linhagem e drift. As demais enriquecem o diagnóstico quando disponíveis.

## Métricas de Drift

O relatório expõe:

- drift de cobertura de features;
- drift de missingness de features;
- drift simples de distribuição de features quando estatísticas tabulares existem;
- drift de distribuição de targets;
- regime de splits walk-forward;
- drift de `rank_ic`, `precision_at_10` e `selected_top_k_expected_value` do Qlib;
- drift de `net_ev_delta_if_applied_research_only`, `precision_reject` e `recall_reject` da IA Shadow.

PnL/outcome não é usado como feature. Ele aparece apenas como target ou evidência de avaliação.

## Classificação de Regime

Cada seção recebe um dos rótulos:

- `stable`
- `degraded`
- `unstable`
- `insufficient_data`

O resumo geral usa a classificação mais conservadora entre as seções.

## Decisão

O monitor nunca autoriza operação.

Regras:

- fonte obrigatória ausente: `status=blocked`;
- dados insuficientes: `status=warning`;
- drift crítico: `status=blocked`;
- cenário estável: `status=ok`;
- `decision=MANTER_EM_RESEARCH` sempre;
- `promotion_eligible=false` sempre.

## CLI

Modo padrão, sem escrita:

```powershell
python .\scripts\build_ai_qlib_drift_regime_monitor_v1.py --project-root . --json
```

Escrita explícita de relatório:

```powershell
python .\scripts\build_ai_qlib_drift_regime_monitor_v1.py --project-root . --write-report --json
```

Com `--write-report`, somente estes artefatos runtime são materializados:

- `data/reports/ai_qlib_drift_regime_monitor_v1.json`
- `data/reports/ai_qlib_drift_regime_monitor_v1.md`

Esses arquivos permanecem fora do Git.

## Safety Flags

O output mantém:

```text
paper_only=true
shadow_only=true
research_only=true
read_only=true
operational_authority=false
readiness_release_authority=false
live_release_allowed=false
canary_release_allowed=false
order_submission_enabled=false
real_order_submission_enabled=false
sends_orders=false
exchange_private_access=false
changes_risk=false
changes_model=false
model_promotion_performed=false
registry_write_performed=false
active_model_changed=false
qlib_runtime_updated=false
ai_shadow_runtime_updated=false
writes_runtime=false
writes_sqlite=false
writes_parquet=false
```

## Limitações

- O monitor depende das estatísticas já presentes nos relatórios existentes.
- Drift de distribuição de features fica `insufficient_data` quando o dataset manifest não expõe estatísticas de referência e atuais.
- O relatório é evidência para revisão, não autorização operacional.

## Por Que Não Há Promoção Automática

Drift/regime é um sinal de estabilidade ou degradação metodológica. Mesmo quando `status=ok`, o resultado apenas indica que as evidências disponíveis não mostram drift crítico. Promoção de modelo, alteração de registry, ativação de veto/runtime ou mudança de risco exigem branches e gates separados.
