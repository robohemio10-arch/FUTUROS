# RiskManager Drawdown e Recovery Modes

Esta branch adiciona uma camada institucional read-only para avaliar drawdown, perdas, incidentes e bloqueios externos antes de recomendar um modo operacional paper/shadow. Ela não altera o `RiskManager` legado, não muda runtime, não altera config, não toca no signal producer, não acessa Freqtrade, não envia ordens e não aumenta risco.

## Modos

Modos suportados:

- `NORMAL`
- `CONSERVATIVE`
- `PROTECTION`
- `PANIC`
- `REDUCE_ONLY`
- `PAUSED`
- `RECONCILING`

`REDUCE_ONLY` existe como estado conceitual de governança. A camada nunca envia ordens reais nem executa redução operacional.

## Fontes

O auditor aceita entradas locais de:

- equity curve;
- closed trades;
- paper session report;
- Market Data Health report;
- readiness/soak report;
- Monte Carlo report;
- event-driven backtest report;
- kill switch status;
- incidents;
- state divergence report.

## Métricas

O relatório calcula:

- `daily_loss_pct`
- `weekly_loss_pct`
- `peak_to_valley_drawdown_pct`
- `current_drawdown_pct`
- `max_drawdown_pct`
- `consecutive_losses`
- `recovery_progress_pct`
- `recommended_mode`
- `previous_mode`
- `transition_reason`
- `allowed_actions`
- `blocked_actions`

## Bloqueios

O auditor bloqueia quando detectar:

- perda diária acima do limite;
- perda semanal acima do limite;
- drawdown acima do limite;
- sequência de perdas acima do limite;
- Market Data Health bloqueado;
- stale data;
- prediction stale;
- backup/restore obrigatório falho;
- divergência de estado;
- kill switch ativo;
- incidente P0/P1 aberto;
- `reconciliation_required=true`;
- safety flags inseguras.

## Transições

Regras principais:

- `NORMAL -> CONSERVATIVE` quando houver warning financeiro ou de mercado.
- `CONSERVATIVE -> PROTECTION` quando drawdown/perdas sequenciais passarem dos limites.
- `PROTECTION -> PANIC` quando houver bloqueio crítico.
- qualquer modo `-> RECONCILING` quando houver divergência de estado.
- qualquer modo `-> PAUSED` quando operador/sistema marcar pausa paper.
- `PANIC` não volta automaticamente para `NORMAL`.
- recuperação exige clean streak e aprovação explícita de recuperação.

## CLI

```powershell
python scripts/run_risk_recovery_mode_audit.py `
  --equity-curve data/reports/equity_curve.parquet `
  --closed-trades data/reports/paper_closed_trades.parquet `
  --paper-session-report data/reports/paper_session_report.json `
  --market-health-report data/reports/market_data_health_audit_report.json `
  --readiness-report data/reports/risk_readiness_soak_dashboard_sources_report.json `
  --monte-carlo-report data/reports/monte_carlo_risk_simulation_report.json `
  --backtest-report data/reports/event_driven_backtest_report.json `
  --kill-switch data/runtime/kill_switch.json `
  --state-divergence-report data/reports/state_divergence_report.json
```

Saída padrão:

- `data/reports/risk_recovery_mode_audit_report.json`

Esse relatório é runtime e não deve ser versionado.

## Segurança

Flags esperadas:

- `paper_only=true`
- `shadow_only=true`
- `live_trading_enabled=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `exchange_private_access=false`
- `sends_orders=false`
- `changes_risk=false`

A IA nunca pode aumentar risco, stake ou alavancagem. `increase_risk`, `increase_stake` e `increase_leverage` permanecem em `blocked_actions` em todos os modos.
