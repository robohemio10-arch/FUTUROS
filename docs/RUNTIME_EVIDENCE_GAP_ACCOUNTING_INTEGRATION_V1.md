# Runtime Evidence Gap Accounting Integration v1

## Objetivo

Integrar a auditoria institucional de gaps do soak paper/shadow ao ecossistema de evidência operacional do SMART FUTUROS, sem alterar risco, modelo, dataset, OCR, configuração de trading ou readiness manual.

## Escopo

Esta branch conecta `paper_shadow_soak_gap_accounting` a três superfícies read-only:

1. `runtime_evidence_pack_v2`
2. `readiness_snapshot_v2`
3. snapshots do SMART FUTUROS Command Center nas abas 6 e 7

## Contrato operacional

A integração permanece estritamente read-only:

- `paper_only=true`
- `shadow_only=true`
- `live_trading_enabled=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `exchange_private_access=false`
- `sends_orders=false`
- `changes_risk=false`
- `changes_model=false`
- `changes_training_dataset=false`
- `writes_trades_master=false`

## Runtime evidence pack

O builder `build_runtime_evidence_pack_and_readiness_snapshot_v2.py` passa a coletar a auditoria de gaps em memória, com `write=False`, por meio de `audit_paper_shadow_soak_continuity_and_gap_accounting`.

A coleta não materializa `paper_shadow_soak_gap_accounting_report.json` automaticamente. O relatório materializado continua opcional e só deve ser escrito por chamada explícita do auditor com `--write`.

Campos integrados ao `readiness_snapshot_v2`:

- `continuous_valid_soak_days`
- `observed_calendar_days`
- `readiness_gap_free`
- `critical_gap_count`
- `warning_gap_count`
- `max_gap_minutes`
- `seven_day_diagnostic_status`
- `thirty_day_readiness_status`
- `paper_shadow_soak_gap_accounting`

## Bloqueios de readiness

A presença de gaps críticos mantém readiness bloqueado. A integração adiciona blockers explícitos:

- `paper_shadow_soak_gap_accounting_blocked`
- `paper_shadow_soak_critical_gaps_present`
- `paper_shadow_soak_not_gap_free`

Esses blockers não liberam canary nem live. Eles formalizam a causa de bloqueio já identificada pela auditoria de continuidade.

## Dashboard

### Aba 6 — Controles Ativos

Adiciona a seção `readiness_gap_accounting`, exibindo:

- soak contínuo válido
- dias calendário observados
- gaps críticos
- gaps warning
- gap máximo em minutos
- status 7d diagnóstico
- status 30d readiness
- `canary_release_allowed=false`
- `live_release_allowed=false`

### Aba 7 — Relatórios Quantitativos & TCA

Adiciona a seção `soak_gap_accounting`, permitindo que a aba quantitativa exponha os bloqueios de continuidade junto das métricas institucionais de performance, risco e TCA.

## Validação esperada

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests/test_runtime_evidence_pack_and_readiness_snapshot_v2.py tests/test_runtime_evidence_pack_v2_observability.py tests/test_runtime_evidence_gap_accounting_integration_v1.py tests/test_dashboard_active_controls_snapshot_builder_v2.py tests/test_dashboard_quantitative_reports_snapshot_builder_v2.py tests/test_dashboard_readiness_gates_snapshot_view_v2.py tests/test_dashboard_streamlit_pages_snapshot_contract_v2.py -q
python scripts/build_runtime_evidence_pack_and_readiness_snapshot_v2.py --project-root . --no-write --json
python scripts/build_runtime_evidence_pack_and_readiness_snapshot_v2.py --project-root . --json
python scripts/build_dashboard_snapshots.py --project-root . --once --strict false --output-dir data/reports --json
python scripts/generate_project_manifest.py --check
python scripts/scan_versioned_secrets.py --json
git ls-files | Select-String "\.(parquet|sqlite|sqlite3|db|csv|xlsx|jsonl|zip)$"
```

## Resultado esperado no estado atual

Enquanto o soak tiver menos de 30 dias contínuos válidos, gaps críticos ou fontes de readiness bloqueadas, o resultado correto continua sendo:

```text
status=blocked
readiness_snapshot_status=blocked
canary_release_allowed=false
live_release_allowed=false
```

Essa branch melhora a rastreabilidade do bloqueio; não relaxa nenhum gate.
