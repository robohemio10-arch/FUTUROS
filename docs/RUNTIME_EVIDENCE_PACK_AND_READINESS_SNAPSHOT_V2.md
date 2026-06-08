# Runtime Evidence Pack and Readiness Snapshot v2

Esta etapa consolida evidencias operacionais existentes em dois artefatos runtime, determinísticos e auditáveis:

- `data/reports/runtime_evidence_pack_v2.json`
- `data/reports/readiness_snapshot_v2.json`

Os arquivos são outputs locais de execução. Eles não devem ser versionados.

## Objetivo

O evidence pack v2 reúne sinais já produzidos pelo ambiente paper/shadow: soak, autoridade do DB paper, readiness gate, Monte Carlo, saúde de market data, scheduler diário da IA Shadow, backup/restore, healthcheck Docker, manifesto limpo e secret scan. O snapshot v2 transforma essas evidências em uma visão única de readiness.

## Fontes Consolidadas

O builder tenta ler, de forma defensiva, relatórios em `data/reports/`, incluindo:

- `paper_soak_report.json`
- `freqtrade_paper_db_authority_report.json`
- `readiness_gate_report.json`
- `monte_carlo_risk_simulation_report.json`
- `monte_carlo_risk_budget_policy_report.json`
- `market_data_health_audit_report.json`
- `system_healthcheck_report.json`
- `backup_snapshot_report.json`
- `restore_dry_run_report.json`
- `daily_ai_shadow_update_summary.json`
- `ai_shadow_daily_update_scheduler_audit_report.json`

Também executa verificações seguras em memória para:

- `PROJECT_MANIFEST_CLEAN.json`
- `scripts/scan_versioned_secrets.py`

Se uma fonte estiver ausente, ela entra em `missing_evidence`. JSON inválido ou relatório bloqueado entra como evidência inválida/bloqueada.

## Status

- `ready`: evidências obrigatórias completas, sem bloqueios e soak mínimo atingido.
- `blocked`: existe bloqueio de readiness, flag insegura, soak menor que 30 dias, Monte Carlo bloqueado/no-trade ou evidência obrigatória ausente/falha.
- `degraded`: não há bloqueios obrigatórios, mas existem avisos.
- `evidence_missing`: usado pelo evidence pack quando há ausência de evidência sem necessariamente inventar estado operacional.

## Soak

O snapshot separa dois marcos:

- `diagnostic_soak_days=7`: diagnóstico operacional.
- `required_soak_days=30`: requisito mínimo de readiness.

Mesmo que 7 dias sejam atingidos, `live_release_allowed` permanece `false`. Readiness só pode ser considerada depois de 30 dias e sem bloqueios.

## Segurança

Todos os outputs carregam flags explícitas:

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

O builder não acessa exchange privada, não envia ordens, não altera Freqtrade DB, não altera datasets, não altera modelos e não promove modelo.

## Uso

Gerar artefatos runtime:

```powershell
python .\scripts\build_runtime_evidence_pack_and_readiness_snapshot_v2.py `
  --project-root . `
  --output-dir .\data\reports `
  --json
```

Executar apenas como auditoria sem escrever:

```powershell
python .\scripts\build_runtime_evidence_pack_and_readiness_snapshot_v2.py `
  --project-root . `
  --output-dir .\data\reports `
  --no-write `
  --json
```

## Uso em Auditoria

O auditor deve verificar:

- `status`
- `missing_evidence`
- `blocking_reasons`
- `observed_soak_days`
- `diagnostic_soak_reached`
- `readiness_soak_reached`
- `safety_flags`
- `live_release_allowed=false`

O snapshot prepara o acompanhamento de 30 dias paper/shadow e um futuro canário controlado, mas não libera live por si só.
