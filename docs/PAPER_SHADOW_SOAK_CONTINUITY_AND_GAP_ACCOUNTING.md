# SMART FUTUROS — Paper/Shadow Soak Continuity and Gap Accounting

## Objetivo

Esta frente adiciona uma auditoria read-only de continuidade do soak paper/shadow com contabilidade explícita de gaps por janela, cobertura diária/hora e separação entre diagnóstico de 7 dias e readiness de 30 dias.

## Arquivos

- `smartcrypto/ops/paper_shadow_soak_gap_accounting/`
- `scripts/audit_paper_shadow_soak_continuity_and_gap_accounting.py`
- `tests/test_paper_shadow_soak_gap_accounting_*.py`

## Contrato de segurança

A auditoria não executa ordens, não acessa exchange privada, não altera risco, não altera modelo, não altera datasets, não executa OCR, não limpa SQLite e não libera canary/live.

Flags fixas:

- `paper_only=true`
- `shadow_only=true`
- `live_trading_enabled=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `exchange_private_access=false`
- `sends_orders=false`
- `sends_notifications=false`
- `changes_risk=false`
- `live_release_allowed=false`
- `canary_release_allowed=false`
- `manual_go_no_go_required=true`

## Evidências consumidas

A auditoria lê, quando presentes:

- `data/reports/paper_shadow_soak_report.json`
- `data/reports/paper_shadow_soak_continuity_audit.json`
- `data/reports/paper_shadow_soak_anchor_continuity_pack.json`
- `data/reports/runtime_evidence_pack_v2.json`
- `data/reports/readiness_snapshot_v2.json`
- `data/reports/paper_soak_report.json`
- `data/reports/freqtrade_paper_db_authority_report.json`
- `data/reports/monte_carlo_risk_simulation_report.json`
- `data/reports/dashboard_snapshot_build_summary.json`
- `docs/SMART_FUTUROS_DASHBOARD_SEMANTIC_COVERAGE_AUDIT_V2.md`

## Saída

Por padrão, a CLI é read-only e não escreve arquivo. Com `--write`, materializa:

- `data/reports/paper_shadow_soak_gap_accounting_report.json`

Esse arquivo é runtime e não deve ser versionado.

## Campos principais

- `observed_calendar_days`
- `observed_active_days`
- `continuous_valid_soak_days`
- `effective_soak_start_utc`
- `effective_soak_end_utc`
- `covered_intervals`
- `gap_windows`
- `hourly_coverage`
- `daily_coverage`
- `critical_gap_count`
- `warning_gap_count`
- `max_gap_minutes`
- `seven_day_diagnostic_status`
- `thirty_day_readiness_status`
- `readiness_gap_free`
- `blocking_reasons`
- `next_required_actions`

## Interpretação

- 7 dias: diagnóstico operacional inicial.
- 30 dias: requisito mínimo de readiness.
- 30 dias não liberam canary/live automaticamente.
- Qualquer gap crítico bloqueia readiness.
- O output `blocked` é esperado enquanto a janela contínua não atingir 30 dias ou enquanto houver gaps críticos.

## Uso

```powershell
python scripts/audit_paper_shadow_soak_continuity_and_gap_accounting.py --project-root . --json
```

Para materializar runtime output:

```powershell
python scripts/audit_paper_shadow_soak_continuity_and_gap_accounting.py --project-root . --write --json
```

## Validação

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests/test_paper_shadow_soak_gap_accounting_contracts_v1.py tests/test_paper_shadow_soak_gap_accounting_auditor_v1.py tests/test_paper_shadow_soak_gap_accounting_script_v1.py tests/test_paper_shadow_soak_gap_accounting_static_safety_v1.py -q
python scripts/generate_project_manifest.py --check
python scripts/scan_versioned_secrets.py --json
```
