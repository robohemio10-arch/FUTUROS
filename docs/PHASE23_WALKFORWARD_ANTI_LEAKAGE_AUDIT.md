# Phase 23 Walk-Forward Anti-Leakage Audit

## Objetivo

A Fase 23 audita datasets, features, targets, splits temporais, metricas
perfeitas e baselines antes de qualquer confianca estatistica em resultados de
walk-forward. O handover da Fase 21 apontou metricas perfeitas
(`accuracy=1.0`, `precision=1.0`, `recall=1.0`, `f1=1.0`, `roc_auc=1.0`) como
metodologicamente suspeitas ate auditoria formal.

Esta fase e offline, deterministica e paper/shadow only.

## O Que E Bloqueado

A auditoria retorna `status=blocked` quando encontra:

- `future_ret_*` usado como feature;
- `target_*` usado como feature;
- `label_*` usado como feature sem ser o target declarado;
- `pnl`, `return_pct`, `outcome`, `realized_pnl` ou derivados futuros usados
  como feature;
- timestamp de feature posterior ao timestamp de decisao;
- split temporal nao estrito;
- sobreposicao temporal entre treino e teste;
- amostra insuficiente de treino ou teste;
- embargo obrigatorio ausente ou menor que o configurado;
- features de fechamento (`close_*`) quando a decisao e em abertura;
- safety flags inseguras.

Metricas perfeitas sem explicacao metodologica geram `warning` em modo normal e
`blocked` em modo `--strict`.

## Baselines Obrigatorios

Um relatorio walk-forward confiavel precisa comparar o modelo contra:

- `random`;
- `always_long`;
- `always_short`;
- `no_trade`.

Baselines ausentes geram `warning`; em modo `--strict`, podem bloquear.

## Relatorio

O relatorio runtime padrao e:

```text
data/reports/phase23_anti_leakage_report.json
```

Ele contem:

- `status`;
- `reason`;
- `audited_at_utc`;
- `input_path`;
- `rows`;
- `columns`;
- `feature_columns`;
- `target_columns`;
- `prohibited_feature_columns`;
- `lookahead_columns`;
- `temporal_split_valid`;
- `embargo_required`;
- `embargo_present`;
- janelas temporais de treino/teste;
- `overlap_detected`;
- `suspicious_perfect_metrics`;
- `missing_baselines`;
- `baseline_requirements`;
- `leakage_findings`;
- `warnings`;
- `blocking_findings`;
- flags de seguranca paper/shadow only.

Arquivos em `data/`, `models/`, `reports/`, parquet, sqlite, csv, xlsx, logs e
evidence nao devem ser versionados.

## Uso

Auditar dataset local:

```powershell
python .\scripts\run_phase23_anti_leakage_audit.py `
  --dataset data/features/training_dataset.parquet `
  --report data/reports/phase23_anti_leakage_report.json `
  --timestamp-column open_ts `
  --target-column target_win `
  --decision-time-column open_ts `
  --min-train-rows 100 `
  --min-test-rows 30 `
  --require-embargo `
  --embargo-minutes 60 `
  --strict
```

Auditar tambem um relatorio walk-forward existente:

```powershell
python .\scripts\run_phase23_anti_leakage_audit.py `
  --dataset data/features/training_dataset.parquet `
  --walkforward-report data/reports/phase21_qlib_walkforward_report.json `
  --report data/reports/phase23_anti_leakage_report.json `
  --timestamp-column open_ts `
  --target-column target_win `
  --strict
```

## Garantias De Seguranca

A Fase 23:

- nao habilita live trading;
- nao habilita `ORDER_SUBMISSION_ENABLED`;
- nao habilita `REAL_ORDER_SUBMISSION_ENABLED`;
- nao acessa exchange privada;
- nao envia ordens;
- nao altera Freqtrade DB;
- nao altera `trades_master`;
- nao altera `training_dataset.parquet`;
- nao altera signal producer;
- nao altera runtime Qlib;
- nao altera registry automaticamente;
- nao promove modelo automaticamente;
- nao altera modelos;
- nao altera risco;
- nao altera Docker;
- nao altera `.env`.
