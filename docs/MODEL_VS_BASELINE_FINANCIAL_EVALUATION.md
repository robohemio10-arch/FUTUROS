# Model vs Baseline Financial Evaluation

Esta avaliacao e offline/research/shadow. Ela nao habilita live trading, nao envia ordens, nao altera o `START_PAPER_24H` e nao autoriza aumento de risco.

## Objetivo

Comparar modelos reais simples contra baselines financeiros em um conjunto finance-grade. A avaliacao responde se um modelo treinado apenas com features conhecidas no momento de abertura supera alternativas triviais como operar sempre, operar aleatoriamente ou ficar em caixa.

## Baseline vs modelo real

Baselines sao estrategias de referencia:

- `always_predict_win`: opera todas as linhas como se fossem vencedoras.
- `majority_class`: usa a classe majoritaria do treino.
- `random_strategy`: decide com seed fixa.
- `no_trade/cash`: nao opera.

Modelos reais sao treinados em folds walk-forward:

- `logistic_regression`
- `random_forest`
- `gradient_boosting`

Cada modelo gera probabilidade e e avaliado em thresholds configuraveis.

## Por que usar apenas finance-grade

O sidecar finance-grade remove linhas com `leverage_missing`, `price_return_extreme`, `net_return_extreme` e outros bloqueios finais. Isso evita que dados financeiros ruins entrem nas metricas. As linhas rejeitadas continuam auditaveis em arquivo separado e nao sao apagadas silenciosamente.

## Walk-forward com embargo

Cada fold treina no passado e testa no futuro. O embargo remove registros muito proximos do inicio do teste, reduzindo risco de vazamento temporal entre treino e teste.

## Features proibidas

Nao entram no treinamento:

- `target_win`
- `return_pct`, `net_return_pct`, `gross_return_pct`, `leveraged_return_pct`
- `pnl`, `pnl_resolved`
- `raw_return`, `raw_return_resolved`
- `exit_price`, `exit_price_repaired`
- `close_*`
- `mfe_pct`, `mae_pct`
- `path_candles`
- qualquer `future_ret_*` ou `target_*`

O seletor usa apenas features numericas de abertura, como `open_1m_*` e `open_5m_*`, excluindo timestamps.

## Metricas

As metricas por fold, modelo e threshold incluem:

- `accuracy`
- `precision`
- `recall`
- `trades`
- `win_rate`
- `average_net_return_pct`
- `total_net_return_pct`
- `profit_factor`
- `max_drawdown`

O ranking usa retorno liquido total, profit factor, drawdown e numero de trades. Se nenhum modelo superar o melhor baseline, o status e `WARNING`.

## Interpretacao

- `OK`: pelo menos um modelo superou o melhor baseline na avaliacao offline finance-grade.
- `WARNING`: a avaliacao rodou, mas o modelo nao superou baseline ou ha limitacao relevante.
- `BLOCKED`: sidecar bloqueado, leakage detectado ou entradas inconsistentes.

Mesmo `OK` nao libera live trading. O resultado apenas informa pesquisa/shadow e deve passar por governanca, drift, risk manager, command bus, ledger, preflight, kill switch e financial event log antes de qualquer ciclo operacional paper.

## Comando recomendado

```powershell
python scripts/run_model_vs_baseline_financial_evaluation.py `
  --features data/features/training_dataset_open_decision_clean.parquet `
  --sidecar data/features/training_normalized_return_sidecar.parquet `
  --sidecar-report data/reports/normalized_return_sidecar_report.json `
  --output-report data/reports/model_vs_baseline_financial_evaluation_report.json `
  --id-column trade_id `
  --target-column target_win `
  --return-column net_return_pct `
  --time-column open_1m_ts `
  --folds 5 `
  --embargo-minutes 60 `
  --seed 42 `
  --min-train-rows 200 `
  --min-test-rows 100 `
  --probability-thresholds 0.50,0.55,0.60,0.65,0.70
```
