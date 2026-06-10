# Runtime Evidence Pack e Readiness Snapshot v2 — Observabilidade operacional

## Objetivo

Consolidar um snapshot institucional read-only de evidências paper/shadow, sem executar ordens, sem acessar exchange privada, sem alterar risco, sem alterar modelos e sem escrever datasets oficiais.

## Arquivos

- `smartcrypto/ops/runtime_evidence_pack.py`
- `scripts/build_runtime_evidence_pack_and_readiness_snapshot_v2.py`
- `tests/test_runtime_evidence_pack_v2_observability.py`
- `data/reports/runtime_evidence_pack_v2.json`
- `data/reports/readiness_snapshot_v2.json`

## Fontes principais

Evidências de readiness:

- `paper_soak_report.json`
- `paper_shadow_soak_report.json`
- `paper_shadow_soak_continuity_audit.json`
- `freqtrade_paper_db_authority_report.json`
- `readiness_gate_report.json`
- `monte_carlo_risk_simulation_report.json`
- `monte_carlo_no_trade_recovery_diagnostics.json`
- `runtime_safety_config_validation_report.json`
- `critical_alerting_report.json`
- `ai_shadow_*`

Evidências runtime:

- `trade_event_notifications_report.json`
- `phase14_runtime_feedback_sync_report.json`
- `phase14_summary.json`
- `phase14_output_summary.json`
- `phase14_open_positions_report.json`
- `phase14_closed_feedback_report.json`
- `qlib_paper_refresh_supervisor_report.json`
- `qlib_market_features_refresh_report.json`
- `qlib_fresh_prediction_runner_report.json`
- `phase13_signal_producer_report.json`
- `ntfy_telegram_manual_validation_summary.json`

## Containers esperados

- `freqtrade-paper`
- `phase14-feedback-sync-paper`
- `qlib-refresh-supervisor-paper`
- `smartcrypto-bot-paper`
- `smartcrypto-dashboard-paper`
- `trade-event-notifications-paper`

## Modos

Sem consulta Docker:

python scripts/build_runtime_evidence_pack_and_readiness_snapshot_v2.py --json

Com snapshot best-effort dos containers:

python scripts/build_runtime_evidence_pack_and_readiness_snapshot_v2.py --json --include-containers

Dry-run sem escrita:

python scripts/build_runtime_evidence_pack_and_readiness_snapshot_v2.py --json --no-write

## Safety invariants

- `paper_only=true`
- `shadow_only=true`
- `live_trading_enabled=false`
- `live_release_allowed=false`
- `canary_release_allowed=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `exchange_private_access=false`
- `sends_orders=false`
- `changes_risk=false`

## Interpretação

`runtime_observability.status=ok`:

- todos os componentes runtime observados estão consistentes.

`runtime_observability.status=degraded`:

- algum report opcional está ausente, stale ou inconsistente, sem violação de safety.

`runtime_observability.status=blocked`:

- algum componente runtime declarou status bloqueante ou flag unsafe.

O readiness final permanece bloqueado enquanto não houver 30 dias canônicos de soak, readiness gate aprovado e ausência de P0/P1 live-blocking.
