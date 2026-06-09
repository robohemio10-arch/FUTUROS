# AI Shadow Threshold Readiness Evidence

## Objetivo

Esta frente cria evidência institucional e read-only para avaliar se o threshold da IA Shadow possui base suficiente para readiness.

O relatório não promove modelo, não altera risco, não altera datasets, não executa ordens e não libera live/canário. Ele apenas consolida evidências existentes e expõe status auditável.

## Arquivos

- `smartcrypto/ops/ai_shadow_threshold_readiness.py`
- `scripts/audit_ai_shadow_threshold_readiness.py`
- `tests/test_ai_shadow_threshold_readiness_evidence.py`
- `data/reports/ai_shadow_threshold_readiness_evidence.json` quando a CLI roda com escrita habilitada

## Uso

Sem escrita:

```powershell
python .\scripts\audit_ai_shadow_threshold_readiness.py --no-write --json
```

Com escrita padrão:

```powershell
python .\scripts\audit_ai_shadow_threshold_readiness.py
```

Parâmetros principais:

```powershell
python .\scripts\audit_ai_shadow_threshold_readiness.py `
  --min-decisions 500 `
  --min-acceptance-rate 0.01 `
  --max-acceptance-rate 0.99 `
  --min-profit-factor 1.0
```

## Status

- `evidence_missing`: nenhuma evidência central AI Shadow foi encontrada.
- `blocked`: amostra insuficiente, contagens ausentes, métrica financeira abaixo do piso ou bloqueio de schema/drift/safety.
- `degraded`: evidência presente, sem bloqueio direto, mas com warning/categorias diagnósticas.
- `ok`: evidência central suficiente, métricas dentro dos limites e sem violações detectadas.

Mesmo em `ok`:

```text
live_release_allowed=false
canary_release_allowed=false
```

## Métricas extraídas

O relatório tenta extrair, quando disponíveis:

- `accepted_decisions`
- `rejected_decisions`
- `total_decisions`
- `acceptance_rate`
- `profit_factor`
- `net_pnl`
- `max_drawdown`

As métricas vêm de relatórios JSON existentes em `data/reports/`. O módulo tolera arquivos ausentes e JSON inválido, reportando `missing_evidence` e `invalid_evidence`.

## Evidências lidas

- `data/reports/ai_shadow_filter_decision_db_audit_summary.json`
- `data/reports/ai_shadow_filter_incremental_daily_summary.json`
- `data/reports/ai_shadow_financial_threshold_evaluation_report.json`
- `data/reports/daily_ai_shadow_update_summary.json`
- `data/reports/ai_shadow_drift_monitor_report.json`
- `data/reports/runtime_evidence_pack_v2.json`
- `data/reports/readiness_snapshot_v2.json`
- `data/reports/paper_shadow_soak_continuity_audit.json`
- `data/reports/monte_carlo_no_trade_recovery_diagnostics.json`

## Safety invariants

O relatório fixa:

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
  "changes_model": false,
  "promotes_model": false,
  "live_release_allowed": false
}
```

Qualquer evidência contraditória bloqueia o status.

## Fora de escopo

Esta branch não executa trading, não envia ordens, não acessa exchange privada, não altera risco, não altera `trades_master`, não reconstrói datasets, não promove modelo, não muda threshold operacional e não habilita canário/live.
