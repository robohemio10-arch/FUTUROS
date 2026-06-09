# Financial Event Log e Critical Alerting

Esta branch adiciona um log financeiro estruturado e um sistema local de alertas críticos para o FUTUROS/SmartCrypto. A camada é estritamente paper/shadow only e não altera operação, risco, config, signal producer, Freqtrade, registry ou modelos.

## FinancialEventLog

Módulo:

- `smartcrypto/ops/financial_event_log.py`

O log é append-only em JSONL. Caminho runtime padrão:

- `data/reports/financial_event_log.jsonl`

Esse arquivo não deve ser versionado.

Cada evento contém:

- `event_id`
- `correlation_id`
- `event_type`
- `event_severity`
- `event_status`
- `occurred_at_utc`
- `source`
- `symbol`
- `side`
- `model_id`
- `model_version`
- `config_version`
- `risk_mode`
- `runtime_mode`
- `state_before`
- `state_after`
- `reason`
- `metadata`
- flags de segurança paper/shadow

O logger valida schema antes de gravar. Ele bloqueia tipo de evento inválido, severidade inválida, `correlation_id` ausente, timestamp inválido e flags inseguras como live trading, order submission, acesso privado, envio de ordens ou alteração de risco.

## Tipos de evento

Tipos institucionais suportados incluem:

- `signal_generated`
- `signal_rejected`
- `risk_approved`
- `risk_rejected`
- `capital_reserved`
- `capital_released`
- `order_intent_created`
- `order_submitted_simulated`
- `order_rejected_simulated`
- `state_divergence_detected`
- `kill_switch_triggered`
- `drift_detected`
- `prediction_stale`
- `market_data_stale`
- `spread_blocked`
- `liquidity_blocked`
- `latency_blocked`
- `backup_failed`
- `restore_failed`
- `model_promotion_blocked`
- `registry_updated_shadow`
- `paper_session_started`
- `paper_session_blocked`
- `reconciliation_required`

## Agregação

O log pode ser lido e filtrado por:

- `event_type`
- `severity`
- `symbol`
- `correlation_id`
- `status`
- intervalo temporal

O resumo expõe:

- `total_events`
- `critical_events`
- `warning_events`
- `blocked_events`
- `latest_event_at_utc`
- `events_by_type`
- `events_by_severity`
- `open_incidents`
- `correlation_ids_count`

## CriticalAlerting

Módulo:

- `smartcrypto/ops/critical_alerting.py`

Ele consome `financial_event_log.jsonl` e gera relatório runtime:

- `data/reports/critical_alerting_report.json`

Esse JSON não deve ser versionado.

Alertas críticos são gerados para kill switch, divergência de estado, falha de backup/restore, market data stale crítico, spread/liquidity/latency bloqueados, reconciliação obrigatória, flags inseguras e repetição excessiva de `risk_rejected` ou `prediction_stale`.

O alerting é somente leitura: não envia email, Telegram, ordem, nem altera runtime, risco ou config.

## CLI

```powershell
python scripts/run_financial_event_log_audit.py `
  --event-log data/reports/financial_event_log.jsonl `
  --alert-report data/reports/critical_alerting_report.json `
  --max-risk-rejections 5 `
  --max-prediction-stale 3 `
  --strict
```

O CLI valida o log, gera resumo e grava o relatório de alertas local.

## Segurança

Flags esperadas:

- `paper_only=true`
- `shadow_only=true`
- `runtime_mode=paper`
- `live_trading_enabled=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `exchange_private_access=false`
- `sends_orders=false`
- `changes_risk=false`

Nenhum arquivo em `data/`, `models/`, `reports/`, parquet, SQLite, CSV, XLSX, logs ou evidence deve ser versionado.

## Entrega externa por ntfy e Telegram

A camada de entrega externa foi isolada em:

- `smartcrypto/ops/notification_channels.py`
- `scripts/run_critical_notification_dispatch.py`

Ela consome o relatório `data/reports/critical_alerting_report.json` e, se houver status `blocked`, `warning` ou `missing_data`, envia resumo para ntfy e/ou Telegram quando os canais estiverem explicitamente habilitados por variáveis de ambiente.

Arquivo de configuração documental:

- `config/critical_notifications.example.yml`

Documentação operacional:

- `docs/NTFY_TELEGRAM_CRITICAL_NOTIFICATIONS.md`

A entrega externa continua paper/shadow only e preserva `sends_orders=false` e `changes_risk=false`.
