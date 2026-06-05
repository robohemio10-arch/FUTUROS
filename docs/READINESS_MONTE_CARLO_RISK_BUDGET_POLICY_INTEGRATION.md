# Readiness Monte Carlo Risk Budget Policy Integration

Esta integração faz o ciclo de readiness reconhecer `data/reports/monte_carlo_risk_budget_policy_report.json`.

O objetivo não é aprovar Monte Carlo ruim. O objetivo é distinguir risco não tratado de risco tratado por uma política conservadora `no_trade`.

## Classificação

Quando o Monte Carlo bruto está `blocked` e a policy report é segura com `policy_action=no_trade`:

- `no_trade_policy_present=true`;
- `monte_carlo_risk_treated=true`;
- `monte_carlo_risk_budget_policy_action=no_trade`;
- `readiness_approved=false`;
- `readiness_may_proceed=false`;
- `live_release_allowed=false`;
- o blocker muda de `monte_carlo_blocked` para `monte_carlo_no_trade_policy_active` no final audit.

Isso registra que o risco foi formalmente endereçado com não operar, sem liberar live e sem elevar readiness.

## Segurança

Se a policy report declarar qualquer flag insegura, a integração bloqueia como `unsafe_policy_report`:

- `live_release_allowed=true`;
- `sends_orders=true`;
- `order_submission_enabled=true`;
- `real_order_submission_enabled=true`;
- `exchange_private_access=true`.

## CLIs

Readiness:

```powershell
python scripts/run_readiness_gate_audit.py `
  --monte-carlo-report data/reports/monte_carlo_risk_simulation_report.json `
  --monte-carlo-risk-budget-policy-report data/reports/monte_carlo_risk_budget_policy_report.json
```

Paper/shadow soak:

```powershell
python scripts/build_paper_shadow_soak_report.py `
  --monte-carlo-report data/reports/monte_carlo_risk_simulation_report.json `
  --monte-carlo-risk-budget-policy-report data/reports/monte_carlo_risk_budget_policy_report.json
```

Dashboard sources:

```powershell
python scripts/inspect_risk_readiness_soak_sources.py `
  --monte-carlo-report data/reports/monte_carlo_risk_simulation_report.json `
  --monte-carlo-risk-budget-policy-report data/reports/monte_carlo_risk_budget_policy_report.json
```

Final audit:

```powershell
python scripts/build_final_technical_audit_report.py `
  --monte-carlo-risk-budget-policy-report data/reports/monte_carlo_risk_budget_policy_report.json
```

## Garantias

- Paper/shadow only.
- Não habilita live.
- Não habilita order submission.
- Não acessa exchange privada.
- Não envia ordens.
- Não altera Freqtrade DB.
- Não altera `trades_master`.
- Não altera `training_dataset.parquet`.
- Não altera modelos, registry, signal producer ou risco operacional real.
- Não altera `.env`.
