# Outcome Sidecar Financial Evaluation

O dataset limpo de decisao em abertura remove `return_pct`, `mfe_pct`,
`mae_pct`, `pnl` e colunas `close_*` para impedir vazamento no treino. Essas
colunas continuam uteis para avaliar resultado financeiro, mas nao podem voltar
como features.

## Por Que Sidecar

O outcome sidecar separa resultado realizado do dataset de treino:

- features limpas permanecem no arquivo de treino/scoring;
- outcomes financeiros ficam em arquivo separado;
- o join por `trade_id` acontece somente na avaliacao offline;
- `return_pct`, `mfe_pct`, `mae_pct` e `pnl` nunca entram como input do modelo.

## Feature, Label, Metadata E Outcome

- Feature: informacao disponivel no momento da decisao, como `open_1m_*`.
- Label: alvo supervisionado, por padrao `target_win`.
- Metadata: identificadores e tempo, como `trade_id`, `symbol`, `open_1m_ts`.
- Outcome: resultado conhecido depois do trade, como `return_pct`.

## Gerar Sidecar

```bash
python scripts/build_outcome_sidecar.py \
  --input data/features/training_dataset.parquet \
  --output data/features/training_outcome_sidecar.parquet \
  --report data/reports/outcome_sidecar_report.json
```

O sidecar contem apenas `trade_id`, `symbol`, `open_1m_ts`, `target_win`,
`return_pct`, `mfe_pct`, `mae_pct` e `pnl` quando existir.

## Avaliar Financeiramente

```bash
python scripts/run_sidecar_financial_evaluation.py \
  --features data/features/training_dataset_open_decision_clean.parquet \
  --sidecar data/features/training_outcome_sidecar.parquet \
  --output-report data/reports/sidecar_financial_evaluation_report.json \
  --id-column trade_id \
  --target-column target_win \
  --return-column return_pct \
  --folds 5 \
  --embargo-minutes 60 \
  --seed 42
```

Baselines avaliados:

- `random_strategy`
- `always_predict_win`
- `always_predict_loss`
- `majority_class`
- `no_trade/cash`

## Interpretacao

- `OK`: features sem leakage bloqueante e sidecar juntado integralmente.
- `WARNING`: sidecar sem algumas colunas opcionais de outcome.
- `BLOCKED`: coluna financeira ou `close_*` apareceu nas features, ou o join
  por `trade_id` perdeu linhas.

## Politica Operacional

Esta avaliacao e offline/research only. Ela nao chama exchange, nao le conta
privada, nao envia ordens, nao altera risco, nao libera live trading e nao deve
entrar no caminho critico do `START_PAPER_24H`.
