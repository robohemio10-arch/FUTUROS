# Open Decision Clean Dataset

O dataset original `data/features/training_dataset.parquet` foi bloqueado pela
Fase 23 porque misturava features disponiveis no momento da abertura com
informacoes conhecidas apenas depois do trade:

- `return_pct`
- `mfe_pct` e `mae_pct`
- `close_1m_*`
- `close_5m_*`
- eventuais `future_ret_*`, `target_*` auxiliares e `pnl`

Essas colunas inflariam metricas de pesquisa e podem explicar resultados
perfeitos ou quase perfeitos.

## O Que Permanece

Metadados:

- `trade_id`
- `symbol`
- `open_1m_ts`
- `open_5m_ts`

Label:

- `target_win`

Features permitidas:

- `open_1m_*`, exceto timestamps
- `open_5m_*`, exceto timestamps

`path_candles` e removido por padrao. Ele so entra quando
`--allow-path-candles` for explicitamente usado.

## O Que E Removido

- `close_1m_*`
- `close_5m_*`
- `return_pct`, `mfe_pct`, `mae_pct`
- `future_ret_*`
- `target_*` diferente da label configurada
- `pnl` e derivados
- `duration_seconds`, por ser suspeito no momento de abertura

Outcomes podem aparecer no relatorio como colunas removidas, mas nao entram no
dataset final nem nas features.

## Gerar Dataset Limpo

```bash
python scripts/build_open_decision_clean_dataset.py \
  --input data/features/training_dataset.parquet \
  --output data/features/training_dataset_open_decision_clean.parquet \
  --report data/reports/open_decision_clean_dataset_report.json \
  --target-column target_win \
  --decision-mode open
```

## Rodar Fase 23 No Dataset Limpo

```bash
python scripts/run_phase23_anti_leakage_audit.py \
  --dataset data/features/training_dataset_open_decision_clean.parquet \
  --target-column target_win \
  --time-column open_1m_ts \
  --decision-mode open \
  --folds 5 \
  --embargo-minutes 60 \
  --seed 42 \
  --output-report data/reports/phase23_open_decision_clean_report.json \
  --output-feature-audit data/reports/phase23_open_decision_clean_feature_audit.json \
  --output-walkforward data/reports/phase23_open_decision_clean_walkforward.json
```

## Politica Operacional

Este builder e apenas pesquisa offline. Ele nao chama exchange, nao le conta
privada, nao envia ordem, nao altera risco, nao libera live trading e nao deve
entrar no caminho critico do `START_PAPER_24H`.
