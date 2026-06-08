# Risk recovery refresh source parity

Esta frente alinha `scripts/refresh_runtime_evidence_reports.py` com o contrato
de `run_risk_recovery_mode_audit.py`. O refresh automatico agora passa ao auditor
as mesmas fontes runtime que o auditor aceita diretamente, evitando regressao
para `missing_runtime_sources` quando os artefatos existem localmente.

## Fontes aceitas

O refresh passa para o risk recovery:

- `--equity-curve`
- `--closed-trades`
- `--paper-session-report`
- `--market-health-report`
- `--readiness-report`
- `--monte-carlo-report`
- `--backtest-report`
- `--kill-switch`
- `--incidents`
- `--state-divergence-report`

`--state-divergence-report` e opcional. Se nao for informado, o refresh usa o
mesmo caminho de `--state-reconciliation-report` apos a tentativa de atualizacao
desse relatorio.

## Diagnostico no refresh report

`data/reports/runtime_evidence_refresh_report.json` passa a expor:

- `risk_recovery_sources_passed`
- `risk_recovery_optional_sources_missing`
- `risk_recovery_source_status`
- `risk_recovery_reason`

Fontes opcionais ausentes continuam permitidas e aparecem como diagnostico. Elas
nao viram sucesso artificial e nao mascaram readiness.

## Uso

Refresh com defaults institucionais:

```bash
python scripts/refresh_runtime_evidence_reports.py
```

Refresh com fontes explicitas:

```bash
python scripts/refresh_runtime_evidence_reports.py \
  --closed-trades data/feedback/paper_closed_trades_incremental.parquet \
  --paper-session-report data/reports/paper_session_report.json \
  --monte-carlo-report data/reports/monte_carlo_risk_simulation_report.json \
  --backtest-report data/reports/event_driven_backtest_report.json \
  --kill-switch data/runtime/kill_switch.json \
  --incidents data/reports/incidents_report.json \
  --state-divergence-report data/reports/state_reconciliation_audit_report.json
```

## Safety

Esta mudanca e paper/shadow only:

- nao habilita live;
- nao habilita order submission;
- nao envia ordens;
- nao acessa exchange privada;
- nao altera Freqtrade DB;
- nao altera modelos, datasets, stake ou leverage;
- nao versiona `data/`, reports runtime, parquet, sqlite, csv, xlsx, jsonl, logs
  ou evidence.

Mesmo com fontes completas, `live_release_allowed=false`,
`readiness_approved=false`, `order_submission_enabled=false`,
`real_order_submission_enabled=false`, `sends_orders=false` e
`exchange_private_access=false` permanecem preservados.
