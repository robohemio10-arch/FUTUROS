# Paper Autotrain Quarantine Candidate Evaluation V1

## Objetivo

Esta branch cria um avaliador institucional, research-only/read-only, para os
candidatos gerados em quarentena pelo autotreinamento diario paper.

O avaliador responde:

- quais candidatos existem em quarentena;
- se os artefatos existem e sao integros;
- se a linhagem basica do candidato e auditavel;
- se o microbatch usado e suficiente;
- se os criterios estatisticos minimos foram atendidos;
- se gates externos bloqueiam elegibilidade futura;
- se algum candidato pode ir, no maximo, para revisao manual futura.

## Quarentena versus promocao

Treinar em quarentena significa produzir um candidato isolado em
`data/models/quarantine/` e registrar evidencia em
`data/registries/quarantine/`. Esses artefatos nao possuem autoridade
operacional.

Avaliar elegibilidade significa verificar integridade, amostra, balanceamento,
features e gates externos. Esta branch nao promove modelo, nao altera registry
ativo e nao atualiza runtime.

Mesmo quando um candidato passa os criterios minimos, a decisao maxima desta
branch e:

`APROVADO_PARA_REVISAO_MANUAL_FUTURA`

Nunca:

- altera modelo ativo;
- escreve registry ativo;
- atualiza Qlib runtime;
- atualiza IA Shadow runtime;
- altera Freqtrade;
- altera RiskManager;
- escreve sinais operacionais;
- envia ordens.

## Candidatos da Branch 63

A Branch 63 gerou dois candidatos em quarentena:

- `qlib`
- `ai_shadow`

O ciclo confirmado tinha `microbatch_rows=26`. Como o criterio minimo desta
branch e `min_microbatch_rows=100`, a expectativa operacional correta e manter
os candidatos em quarentena por evidencia insuficiente.

## Criterios minimos

Constantes do avaliador:

- `min_microbatch_rows=100`
- `min_class_positive_count=20`
- `min_class_negative_count=20`
- `min_feature_count=5`
- integridade de artefato obrigatoria
- `promotion_eligible` no artefato deve permanecer `false`
- drift gate nao pode estar `blocked`
- execution cost gate nao pode estar `blocked`
- Monte Carlo gate nao pode estar `blocked`
- readiness snapshot nao pode estar `blocked`

## Paths lidos

- `data/registries/quarantine/paper_autotrain_candidate_registry_v1.json`
- `data/models/quarantine/paper_autotrain/**/qlib_candidate_model.json`
- `data/models/quarantine/paper_autotrain/**/ai_shadow_candidate_model.json`
- `data/research/paper_autotrain_daily_quarantine/**/incremental_training_microbatch.parquet`
- `data/reports/paper_autotrain_daily_quarantine_activation_v1.json`
- `data/reports/ai_qlib_drift_regime_monitor_v1.json`
- `data/reports/event_driven_backtest_execution_cost_gate_v1.json`
- `data/reports/monte_carlo_risk_ruin_stress_gate_v1.json`
- `data/reports/readiness_snapshot_v2.json`

Os reports de gates externos sao opcionais. Se ausentes, viram warnings
diagnosticos; se presentes e `blocked`, bloqueiam elegibilidade.

## Paths escritos

Somente com `--write-report`:

- `data/reports/paper_autotrain_quarantine_candidate_evaluation_v1.json`
- `data/reports/paper_autotrain_quarantine_candidate_evaluation_v1.md`

Sem `--write-report`, o CLI e read-only e imprime JSON controlado.

## Paths proibidos

O avaliador nao escreve:

- `data/runtime/`
- SQLite operacional
- Parquet operacional
- registry ativo
- modelo ativo
- `active_freqtrade_signals.json`
- arquivos de sinal operacional
- configs live/canary

## Comandos

```powershell
python .\scripts\build_paper_autotrain_quarantine_candidate_evaluation_v1.py --project-root . --json
python .\scripts\build_paper_autotrain_quarantine_candidate_evaluation_v1.py --project-root . --write-report --json
```

Validacao focada:

```powershell
python -m pytest .\tests\test_paper_autotrain_quarantine_candidate_evaluation_v1.py -q
```

## Interpretacao

Com `microbatch_rows=26`, o resultado esperado e:

- `status=blocked`
- `decision=MANTER_EM_QUARENTENA`
- `eligible_candidate_count=0`
- blocker `min_microbatch_rows_not_met`

Isso nao e falha da branch. E a decisao correta para impedir promocao com
amostra insuficiente.

## Criterios futuros para promocao real

Uma branch futura de promocao precisaria, no minimo:

- amostra acima dos thresholds;
- balanceamento minimo por classe;
- gates externos sem bloqueio;
- avaliacao fora da amostra;
- revisao humana formal;
- registry ativo com contrato separado;
- safety gate especifico para impedir live/order ate aprovacao explicita.

Esta branch nao implementa promocao.
