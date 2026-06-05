# Monte Carlo Risk Budget Position Sizing Policy

Esta política transforma o relatório Monte Carlo em uma recomendação institucional de orçamento de risco e position sizing para o ambiente paper/shadow.

Ela não aprova live, não altera `RiskManager`, não altera stake, não altera leverage, não promove modelo, não escreve sinais e não envia ordens. O objetivo é registrar, de forma auditável, se o risco simulado permite apenas observação, redução de risco ou elegibilidade shadow.

## Entrada

Fonte padrão:

- `data/reports/monte_carlo_risk_simulation_report.json`

O relatório pode conter métricas no topo ou em `risk_metrics`, mantendo compatibilidade com o simulador existente.

## Saída

Artefato runtime, não versionado:

- `data/reports/monte_carlo_risk_budget_policy_report.json`

Campos principais:

- `status`: `ok`, `warning` ou `blocked`;
- `policy_action`: `no_trade`, `observe_only`, `reduce_risk`, `conservative_paper_only` ou `eligible_for_shadow_only`;
- `risk_of_ruin_cap`;
- `max_drawdown_cap_pct`;
- `max_stake_recommended`;
- `max_leverage_recommended`;
- `daily_loss_cap_recommended`;
- `weekly_loss_cap_recommended`;
- `max_consecutive_losses_recommended`;
- `readiness_may_proceed`;
- `live_release_allowed`.

`live_release_allowed` é sempre `false`.

## Interpretação

Monte Carlo bloqueado não é erro técnico. É evidência de risco inaceitável.

Se `expectancy_per_trade` for negativa, a política bloqueia aumento de risco e recomenda `no_trade` ou `observe_only`.

Se `risk_of_ruin` exceder o cap configurado, a política bloqueia readiness.

Se `p95_max_drawdown_pct` exceder o cap configurado, a política bloqueia readiness.

Se `simulated_profit_factor` estiver abaixo do mínimo, a política bloqueia ou alerta conforme severidade e modo `--strict`.

## Uso

```powershell
python scripts/build_monte_carlo_risk_budget_policy.py `
  --monte-carlo-report data/reports/monte_carlo_risk_simulation_report.json `
  --output data/reports/monte_carlo_risk_budget_policy_report.json `
  --risk-of-ruin-cap 0.05 `
  --max-drawdown-cap-pct 40 `
  --min-profit-factor 1.1 `
  --min-expectancy 0 `
  --initial-capital 1000 `
  --current-stake 100 `
  --current-leverage 1
```

Modo estrito:

```powershell
python scripts/build_monte_carlo_risk_budget_policy.py --strict
```

## Garantias De Segurança

- Paper/shadow only.
- Não habilita live trading.
- Não habilita order submission.
- Não habilita real order submission.
- Não acessa exchange privada.
- Não envia ordens.
- Não altera Freqtrade DB.
- Não altera `trades_master`.
- Não altera `training_dataset.parquet`.
- Não altera signal producer.
- Não altera modelos.
- Não promove modelo.
- Não altera risco operacional real.
- Não altera `.env`.

## Readiness

A saída desta política deve ser consumida pelo próximo ciclo de readiness como evidência. Um `status=blocked` deve manter `readiness_may_proceed=false` e `live_release_allowed=false`.

O sistema deve permanecer paper/shadow only enquanto risco de ruína, drawdown, expectancy ou profit factor estiverem fora dos limites institucionais.
