# AI Shadow Model Decision Logger e Outcome Tracker

## Objetivo

Este fluxo registra decisoes de modelos IA Shadow e vincula cada decisao ao
resultado posterior do trade paper fechado. Ele existe para auditoria,
aprendizado supervisionado futuro e avaliacao champion/challenger, sem dar
autoridade operacional para a IA.

## Decision Logger

O logger le metadata do registry ou do relatorio do trainer incremental e um
artefato local de decisoes shadow. A saida append-only fica em:

```text
data/reports/ai_shadow_model_decisions.jsonl
```

Cada linha registra:

- `decision_id`;
- `correlation_id`;
- `model_id`;
- `model_version`;
- `registry_status`;
- `promotion_status`;
- `symbol`;
- `side`;
- `prediction`;
- `probability`;
- `confidence`;
- `threshold`;
- `action_shadow`;
- `reason`;
- `feature_columns_count`;
- `feature_hash`;
- `input_row_hash`;
- `decided_at_utc`;
- `source`;
- flags de seguranca paper/shadow only;
- `sends_orders=false`;
- `changes_risk=false`.

Uso:

```powershell
python .\scripts\log_ai_shadow_model_decisions.py `
  --registry data/models/registry/model_registry.json `
  --trainer-report data/reports/ai_shadow_incremental_trainer_report.json `
  --input data/reports/shadow_decisions.json `
  --output data/reports/ai_shadow_model_decisions.jsonl `
  --report data/reports/ai_shadow_model_decision_logger_report.json `
  --strict
```

## Outcome Tracker

O tracker le `ai_shadow_model_decisions.jsonl` e feedback paper fechado ou o
microbatch incremental. A saida append-only fica em:

```text
data/reports/ai_shadow_model_outcomes.jsonl
```

O relatorio consolidado fica em:

```text
data/reports/ai_shadow_model_outcomes_report.json
```

O match usa:

1. `order_id`, quando existir;
2. `symbol + side + janela temporal`, quando nao houver `order_id`.

Cada outcome registra:

- `decision_id`;
- `correlation_id`;
- `model_id`;
- `model_version`;
- `symbol`;
- `side`;
- `action_shadow`;
- `matched_order_id`;
- `matched`;
- `pnl_fechado`;
- `target_return`;
- `target_profitable`;
- `open_time_utc`;
- `close_time_utc`;
- `outcome_status`;
- `outcome_reason`;
- `tracked_at_utc`;
- flags de seguranca paper/shadow only.

Uso:

```powershell
python .\scripts\track_ai_shadow_outcomes.py `
  --decisions data/reports/ai_shadow_model_decisions.jsonl `
  --feedback data/feedback/paper_closed_trades_incremental.parquet `
  --microbatch data/features/incremental_training_microbatch.parquet `
  --output data/reports/ai_shadow_model_outcomes.jsonl `
  --report data/reports/ai_shadow_model_outcomes_report.json `
  --strict
```

## Status

O logger retorna:

- `ok`: decisoes gravadas no JSONL append-only;
- `blocked`: input ausente, identidade de modelo ausente ou contrato de
  seguranca violado.

O tracker retorna:

- `ok`: outcomes encontrados;
- `no_matches`: tracking executou, mas nenhuma decisao encontrou trade fechado;
- `blocked`: log de decisoes ausente ou contrato de seguranca violado.

## Garantias De Seguranca

Este fluxo:

- e paper/shadow only;
- nao habilita live trading;
- nao habilita `ORDER_SUBMISSION_ENABLED`;
- nao habilita `REAL_ORDER_SUBMISSION_ENABLED`;
- nao acessa exchange privada;
- nao envia ordens;
- nao altera risco;
- nao altera Freqtrade DB;
- nao altera `trades_master`;
- nao altera `training_dataset.parquet`;
- nao altera registry;
- nao altera modelos;
- nao altera signal producer;
- nao altera runtime Qlib;
- nao altera Docker;
- nao altera `.env`.

Os JSON/JSONL gerados em `data/reports/` sao artefatos runtime e nao devem ser
versionados.
