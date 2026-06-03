# AI Shadow Drift Monitor

## Objetivo

O Drift Monitor IA Shadow compara uma distribuicao baseline com uma amostra atual
para detectar mudancas nas features usadas por modelos shadow/challenger. O
resultado e um relatorio formal com status `ok`, `warning` ou `blocked`.

Drift critico bloqueia promocao por status de relatorio, mas nao altera o
registry automaticamente, nao troca modelo, nao altera signal producer e nao
muda risco operacional.

## Entradas

Baseline padrao:

```text
data/models/shadow/ai_shadow_drift_baseline.json
```

Amostra atual padrao:

```text
data/features/incremental_training_microbatch.parquet
```

Contrato opcional:

```text
data/models/shadow/ai_shadow_feature_contract.json
```

Quando o contrato e informado, o monitor usa somente as features numericas
definidas pelo `FeatureContract`.

## Saidas

Relatorio padrao:

```text
data/reports/ai_shadow_drift_monitor_report.json
```

Todos os artefatos em `data/` sao runtime e nao devem ser versionados.

## Metricas Por Feature

Para cada feature, o relatorio inclui:

- `psi`;
- `ks_statistic`;
- `baseline_count`;
- `current_count`;
- `baseline_missing_ratio`;
- `current_missing_ratio`;
- `baseline_mean`;
- `current_mean`;
- `baseline_std`;
- `current_std`;
- `drift_status`;
- `drift_reason`.

## Thresholds Padrao

- `psi_warning=0.10`;
- `psi_blocked=0.25`;
- `ks_warning=0.10`;
- `ks_blocked=0.25`;
- `missing_ratio_warning=0.05`;
- `missing_ratio_blocked=0.20`.

O status global e:

- `ok`: nenhuma feature em warning ou blocked;
- `warning`: pelo menos uma feature em warning e nenhuma blocked;
- `blocked`: pelo menos uma feature blocked ou violacao de contrato/seguranca.

## Bloqueios

O monitor bloqueia quando:

- baseline ausente;
- amostra atual ausente;
- lista de features vazia;
- coluna `future_ret_*` aparece;
- coluna `target_*` e usada como feature;
- feature do contrato esta ausente;
- input tem NaN/infinito acima do limite;
- safety flags sao inseguras;
- `live_trading_enabled=true`;
- `order_submission_enabled=true`;
- `real_order_submission_enabled=true`;
- `exchange_private_access=true`;
- `sends_orders=true`;
- `changes_risk=true`.

## Uso

Construir baseline:

```powershell
python .\scripts\build_ai_shadow_drift_baseline.py `
  --input data/features/incremental_training_microbatch.parquet `
  --contract data/models/shadow/ai_shadow_feature_contract.json `
  --output data/models/shadow/ai_shadow_drift_baseline.json `
  --strict
```

Rodar monitor:

```powershell
python .\scripts\run_ai_shadow_drift_monitor.py `
  --baseline data/models/shadow/ai_shadow_drift_baseline.json `
  --current data/features/incremental_training_microbatch.parquet `
  --contract data/models/shadow/ai_shadow_feature_contract.json `
  --report data/reports/ai_shadow_drift_monitor_report.json `
  --strict
```

## Garantias De Seguranca

Este fluxo e paper/shadow only:

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
- nao altera Docker;
- nao altera `.env`.
