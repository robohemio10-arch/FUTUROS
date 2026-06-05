# Runtime Evidence Refresh e Docker Healthcheck

Esta frente institucionaliza dois controles operacionais do FUTUROS/SmartCrypto em modo paper/shadow:

- renovação controlada de evidências runtime usadas por readiness, soak e healthcheck;
- `HEALTHCHECK` Docker local, sem rede privada, sem ordens e sem acesso ao DB operacional do Freqtrade.

## Escopo

O script `scripts/refresh_runtime_evidence_reports.py` atualiza evidências JSON de operação usando geradores já existentes:

- `critical_alerting_report.json`
- `risk_recovery_mode_audit_report.json`
- `order_intent_capital_ledger_audit_report.json`
- `state_reconciliation_audit_report.json`
- `market_data_health_audit_report.json`, quando a coleta pública de market health não é pulada
- `system_healthcheck_report.json`

O script não altera `trades_master`, `training_dataset.parquet`, modelos, registry, Freqtrade DB, estratégia, stake, leverage ou sinal produtor.

## Healthcheck Docker

Os Dockerfiles em `docker/smartcrypto`, `docker/dashboard` e `docker/qlib` executam:

```bash
python -m smartcrypto.runtime.container_healthcheck --quiet
```

O healthcheck valida apenas:

- import básico do pacote;
- paths locais mínimos;
- flags de segurança.

Ele bloqueia se detectar `LIVE_ENABLED=true`, `ORDER_SUBMISSION_ENABLED=true`, `REAL_ORDER_SUBMISSION_ENABLED=true`, runtime diferente de `paper` ou acesso privado marcado como ativo.

## Diagnóstico de Stale

`smartcrypto.ops.system_healthcheck` agora expõe `stale_reports` como lista estruturada com:

- `report_name`
- `path`
- `age_seconds`
- `age_minutes`
- `stale_limit_seconds`
- `timestamp_key`
- `report_timestamp_utc`

Isso preserva os warnings existentes (`stale_report:<nome>`), mas torna o diagnóstico operacional rastreável.

## Uso

Refresh completo, com coleta pública de market health:

```bash
python scripts/refresh_runtime_evidence_reports.py
```

Refresh sem coleta pública de market health:

```bash
python scripts/refresh_runtime_evidence_reports.py --skip-market-health
```

Healthcheck de container no host:

```bash
python -m smartcrypto.runtime.container_healthcheck
```

System healthcheck:

```bash
python scripts/run_system_healthcheck.py
```

## Garantias de Segurança

- `paper_only=true`
- `shadow_only=true`
- `live_trading_enabled=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `exchange_private_access=false`
- `sends_orders=false`
- `changes_risk=false`

O refresh pode reduzir stale de evidências, mas não transforma uma política de no-trade em liberação. Se readiness ou paper soak estiverem bloqueados por soak insuficiente ou política Monte Carlo de no-trade, o bloqueio permanece no `system_healthcheck_report.json`.
