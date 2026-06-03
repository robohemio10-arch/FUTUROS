# Monte Carlo Risk Simulation

## Objetivo

A simulacao Monte Carlo avalia risco financeiro de estrategia/IA Shadow usando
outcomes, paper trades ou historico tabular equivalente. Ela usa bootstrap com
reposicao, seed fixa e custos/stress deterministicos para estimar distribuicao
de equity, drawdown, risco de ruina, VaR e CVaR.

O relatorio e apenas analitico. Ele nao altera signal producer, registry,
threshold, modelos, risk manager, Freqtrade ou ordens.

## Entradas

Entradas preferenciais:

```text
data/reports/ai_shadow_model_outcomes.jsonl
data/reports/ai_shadow_financial_threshold_evaluation_report.json
```

Tambem aceita parquet, csv, json e jsonl locais para testes e auditoria offline.

Colunas de retorno/PnL sao detectadas nesta ordem:

1. `target_return`
2. `pnl_fechado`
3. `net_pnl`
4. `return`
5. `realized_return`

## Parametros

O CLI aceita:

- `--initial-capital`;
- `--stake`;
- `--leverage`;
- `--fee-bps`;
- `--slippage-bps`;
- `--spread-bps`;
- `--stress-multiplier`;
- `--simulations`;
- `--horizon-trades`;
- `--seed`;
- `--ruin-threshold-pct`;
- `--max-acceptable-drawdown-pct`;
- `--min-trades`;
- `--strict`.

Custos de fee, slippage e spread sao convertidos de bps e descontados por trade.
O `stress_multiplier` multiplica esses custos para cenarios conservadores.

## Metricas

O relatorio inclui:

- `median_final_equity`;
- `mean_final_equity`;
- `min_final_equity`;
- `max_final_equity`;
- `p05_final_equity`;
- `p95_final_equity`;
- `expected_return_pct`;
- `probability_of_loss`;
- `risk_of_ruin`;
- `median_max_drawdown_pct`;
- `p95_max_drawdown_pct`;
- `worst_max_drawdown_pct`;
- `median_max_losing_streak`;
- `p95_max_losing_streak`;
- `var_95`;
- `cvar_95`;
- `simulated_profit_factor`;
- `expectancy_per_trade`;
- custos estressados.

## Recomendacao

`recommendation_status` pode ser:

- `ok`;
- `warning`;
- `blocked`.

O status fica `blocked` quando:

- `risk_of_ruin` excede o limite configurado;
- `p95_max_drawdown_pct` excede o maximo aceitavel;
- `cvar_95` e excessivamente negativo;
- ha trades insuficientes.

Amostra pequena marca `sample_warning=true`.

## Uso

```powershell
python .\scripts\run_monte_carlo_risk_simulation.py `
  --input data/reports/ai_shadow_model_outcomes.jsonl `
  --report data/reports/monte_carlo_risk_simulation_report.json `
  --initial-capital 1000 `
  --stake 100 `
  --leverage 1 `
  --fee-bps 2 `
  --slippage-bps 2 `
  --spread-bps 1 `
  --simulations 1000 `
  --horizon-trades 100 `
  --seed 42 `
  --ruin-threshold-pct 30 `
  --max-acceptable-drawdown-pct 40 `
  --min-trades 30 `
  --strict
```

## Artefato Runtime

Relatorio padrao:

```text
data/reports/monte_carlo_risk_simulation_report.json
```

Esse arquivo e runtime e nao deve ser versionado. Arquivos em `data/`,
`models/`, `reports/`, parquet, sqlite, csv, xlsx, logs e evidence permanecem
fora do git.

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
- nao altera registry automaticamente;
- nao promove modelo automaticamente;
- nao altera modelos;
- nao altera risco;
- nao altera Docker;
- nao altera `.env`.
