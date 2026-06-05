# Paper Soak Evidence e Continuidade No-Trade

Esta frente melhora a evidência institucional do soak paper/shadow e do readiness gate sem remover a política `no_trade` e sem liberar live.

## Por Que Continua Blocked

O estado esperado permanece bloqueado quando houver:

- `monte_carlo_no_trade_policy_active`;
- `soak_days_below_required`;
- `readiness_may_proceed=false`;
- performance financeira negativa ou amostra insuficiente;
- evidências obrigatórias ausentes ou em `missing_data`;
- falha de runtime safety, market health, ledger ou reconciliação.

Monte Carlo `no_trade` é tratado como política conservadora válida. Ele não é reclassificado como falha técnica, mas continua bloqueando readiness e live release.

## Campos Adicionados

`paper_soak_report.json` passa a expor:

- `observed_soak_days` e `observed_soak_hours`;
- `required_soak_days`;
- `remaining_soak_days` e `remaining_soak_hours`;
- `performance_summary`;
- `evidence_quality_summary`;
- `blocking_reasons_by_category`;
- `next_required_actions`;
- `no_trade_exit_requirements`;
- `live_release_allowed=false`;
- `readiness_approved=false`.

`readiness_gate_report.json` passa a diferenciar:

- `blocked_by_policy`;
- `blocked_by_soak_duration`;
- `blocked_by_financial_expectancy`;
- `blocked_by_missing_evidence`;
- `blocked_by_runtime_safety`;
- `blocked_by_market_health`;
- `blocked_by_technical_failure`.

O painel `risk_readiness_soak` passa a expor:

- `paper_days_observed`;
- `paper_days_required`;
- `paper_days_remaining`;
- `no_trade_exit_requirements`;
- `next_collection_targets`;
- `live_release_allowed=false`.

## Caminho Mínimo Para Sair De No-Trade

Sem alterar risco, stake, leverage, modelo ou signal producer, o caminho mínimo é:

1. continuar paper/shadow até completar os dias exigidos de soak;
2. coletar mais fechamentos paper para reduzir amostra insuficiente;
3. melhorar expectancy e Profit Factor em evidência financeira;
4. reduzir risk of ruin dentro do limite Monte Carlo;
5. manter market data, ledger, risk recovery e reconciliação frescos;
6. manter `LIVE_ENABLED=false`, `ORDER_SUBMISSION_ENABLED=false` e `REAL_ORDER_SUBMISSION_ENABLED=false`;
7. passar por revisão manual antes de qualquer discussão de live.

## Garantias

- Não envia ordens.
- Não acessa exchange privada.
- Não altera Freqtrade DB.
- Não altera `trades_master`.
- Não altera `training_dataset.parquet`.
- Não promove modelo.
- Não altera stake/leverage.
- Não remove a política `no_trade` automaticamente.
- Não autoriza live.

## Comandos

```powershell
python scripts/build_paper_shadow_soak_report.py
python scripts/run_readiness_gate_audit.py
python scripts/inspect_risk_readiness_soak_sources.py
python scripts/build_final_technical_audit_report.py
```

Validação de desenvolvimento:

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests/test_paper_shadow_soak_reporting_readiness_gate.py -q
python -m pytest tests/test_dashboard_risk_readiness_soak_panel.py -q
python -m pytest tests/test_monte_carlo_risk_budget_position_sizing_policy.py -q
python -m pytest tests/test_final_technical_audit_20_pillar_reclassification.py -q
python -m pytest -q
```
