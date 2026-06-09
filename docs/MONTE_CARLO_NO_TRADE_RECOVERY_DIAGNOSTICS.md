# Monte Carlo No-Trade Recovery Diagnostics

## Objetivo

Esta frente adiciona um diagnóstico institucional para cenários em que Monte Carlo, readiness ou relatórios correlatos indicam `no_trade`, bloqueio de risco ou evidência insuficiente.

O relatório não desbloqueia live, não relaxa risco e não altera datasets. Ele apenas classifica causas prováveis e recomenda ações de recuperação paper/shadow.

## Arquivos

- `smartcrypto/ops/monte_carlo_no_trade_recovery.py`
- `scripts/audit_monte_carlo_no_trade_recovery.py`
- `tests/test_monte_carlo_no_trade_recovery_diagnostics.py`
- `data/reports/monte_carlo_no_trade_recovery_diagnostics.json` quando a CLI roda com escrita habilitada

## Uso

Sem escrita:

```powershell
python .\scripts\audit_monte_carlo_no_trade_recovery.py --no-write --json
```

Com escrita padrão:

```powershell
python .\scripts\audit_monte_carlo_no_trade_recovery.py
```

Saída padrão:

```text
data/reports/monte_carlo_no_trade_recovery_diagnostics.json
```

## Status

- `evidence_missing`: não há evidência central suficiente.
- `blocked`: há no_trade, violação de safety ou evidência explícita de bloqueio relevante.
- `degraded`: há evidência, mas com ausência parcial ou categorias diagnósticas sem bloqueio direto.
- `ok`: evidência central presente, sem no_trade, sem safety violation e sem warnings.

Mesmo em `ok`:

```text
live_release_allowed=false
canary_release_allowed=false
```

## Categorias de causa raiz

O diagnóstico classifica sinais textuais e campos estruturados em categorias:

- `missing_core_evidence`
- `invalid_evidence`
- `market_data_stale_or_missing`
- `risk_budget_or_drawdown_block`
- `insufficient_sample_or_evidence`
- `soak_or_continuity_block`
- `ai_shadow_quality_gate_block`
- `prediction_or_signal_absence`
- `safety_or_audit_block`

## Fontes de evidência

Quando existirem, são lidos relatórios como:

- `data/reports/monte_carlo_risk_simulation_report.json`
- `data/reports/monte_carlo_risk_budget_policy_report.json`
- `data/reports/runtime_evidence_pack_v2.json`
- `data/reports/readiness_snapshot_v2.json`
- `data/reports/paper_shadow_soak_continuity_audit.json`
- `data/reports/market_data_health_audit_report.json`
- `data/reports/qlib_fresh_predictions_summary.json`
- `data/reports/phase13_active_signals_summary.json`
- `data/reports/ai_shadow_filter_incremental_daily_summary.json`
- `data/reports/ai_shadow_filter_decision_db_audit_summary.json`

Arquivos ausentes entram em `missing_evidence`. JSON inválido entra em `invalid_evidence`. A auditoria é defensiva e não deve quebrar por runtime parcial.

## Safety

O relatório sempre preserva:

```json
{
  "paper_only": true,
  "shadow_only": true,
  "live_trading_enabled": false,
  "order_submission_enabled": false,
  "real_order_submission_enabled": false,
  "exchange_private_access": false,
  "sends_orders": false,
  "changes_risk": false,
  "changes_training_dataset": false,
  "writes_trades_master": false,
  "live_release_allowed": false
}
```

Qualquer evidência contraditória bloqueia o diagnóstico.

## Ações de recuperação

As ações são recomendações operacionais, não alterações automáticas. Exemplos:

- atualizar market features/predictions quando houver staleness;
- acumular mais amostra paper/shadow quando houver evidência insuficiente;
- investigar drawdown, CVaR, risk-of-ruin e stress de custos;
- resolver gaps de soak antes de readiness;
- auditar thresholds AI Shadow sem promover modelo automaticamente;
- validar Qlib predictions e geração de sinais antes de nova simulação.

## Fora de escopo

Esta branch não executa trading, não envia ordens, não acessa exchange privada, não altera risco, não altera `trades_master`, não reconstrói datasets e não habilita canário/live.
