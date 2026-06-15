# SMART FUTUROS — Dashboard Runtime Evidence Integration V1

## Objetivo

Esta branch integra ao SMART FUTUROS Command Center uma visão consolidada e read-only de runtime evidence, readiness, paper runtime health e paper-shadow soak/gap accounting.

A integração não executa producers, não coleta Docker, não chama rede, não envia alertas e não altera arquivos operacionais. Ela apenas lê evidências já materializadas e projeta o estado institucional no dashboard.

## Fontes consumidas

A visão consolidada tenta consumir os seguintes artefatos, sempre de forma defensiva:

- `data/reports/runtime_evidence_pack_v2.json`
- `data/reports/readiness_snapshot_v2.json`
- `data/reports/paper_runtime_health_and_freshness_report.json`
- `data/reports/paper_runtime_container_snapshot_report.json`
- `data/reports/paper_shadow_soak_gap_accounting_report.json`
- `data/reports/paper_shadow_soak_report.json`
- `data/reports/daily_evidence_pack_latest.json`
- `data/reports/runtime_evidence_refresh_report.json`
- `data/reports/dashboard_global_status_snapshot.json`
- `data/reports/dashboard_snapshot_build_summary.json`

Ausência, JSON inválido, staleness e estados bloqueantes viram status controlado. Nenhum valor ausente é sintetizado como OK.

## Diferença entre Source Health e Runtime Evidence

`source_health_matrix` responde se uma fonte existe, está válida, está stale, degrada ou bloqueia o dashboard.

`runtime_evidence_view` responde o que as evidências significam operacionalmente: readiness, runtime health, gaps, soak, canary/live release e bloqueios institucionais.

As duas camadas são complementares. Se `global_source_health_status=BLOCKED`, a runtime evidence integration também permanece conservadora e impede qualquer status global OK.

## Regras de bloqueio

A integração retorna `BLOCKED` quando ocorre qualquer uma das condições abaixo:

- runtime evidence pack requerido ausente ou inválido;
- readiness snapshot requerido ausente ou inválido;
- gap accounting requerido ausente, inválido ou bloqueado;
- `critical_gap_count > 0`;
- `thirty_day_readiness_status` bloqueado ou não atingido;
- source health global bloqueado;
- payload declarar canary/live allowed com cadeia de evidência incompleta ou source health bloqueado;
- fonte requerida stale ou inválida.

## Regras de degradação

A integração retorna `DEGRADED` quando não há bloqueio required, mas há fonte opcional ausente, stale, warning, JSON inválido opcional ou observabilidade incompleta.

## Status controlados

- `OK`: evidência suficiente para observabilidade, sem autorização de live/canary.
- `WARNING`: condição de atenção sem bloqueio institucional.
- `DEGRADED`: observabilidade parcial.
- `BLOCKED`: evidência bloqueante ou condição de readiness insegura.
- `MISSING`: fonte ausente.
- `STALE`: evidência antiga conforme source health.
- `UNKNOWN`: informação insuficiente.
- `DISABLED`: coleta explicitamente desabilitada, como container snapshot opcional.
- `NOT_APPLICABLE`: freshness ou evidência não aplicável.

## Canary, live e orders

A integração sempre projeta:

- `canary_release_allowed=false`
- `live_release_allowed=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`

Se algum payload declarar `true`, o valor bruto é preservado em campo `*_raw`, mas a saída efetiva permanece falsa quando source health/readiness/evidence não formam uma cadeia completa e válida.

## Snapshots atualizados

A branch adiciona campos compatíveis em:

- `dashboard_snapshot_build_summary.json`
- `dashboard_global_status_snapshot.json`
- `dashboard_infrastructure_snapshot.json`
- `dashboard_active_controls_snapshot.json`

Campos principais:

- `runtime_evidence_integration_status`
- `runtime_evidence_view`
- `runtime_evidence_blocking_reasons`
- `runtime_evidence_degraded_reasons`
- `runtime_evidence_missing_sources`
- `runtime_evidence_stale_sources`
- `runtime_evidence_safety_flags`

## UI

A UI Streamlit exibe a visão consolidada de runtime evidence em modo read-only. O componente não executa ação, não chama producer e não altera estado.

## Validação

```powershell
python -m compileall -q scripts smartcrypto tests
python scripts/audit_dashboard_semantic_coverage_v2.py --project-root . --json
python scripts/build_dashboard_snapshots.py --project-root . --output-dir data/reports --strict false --once --json
$DashboardTests = Get-ChildItem ".\tests" -Filter "test_dashboard_*.py" | ForEach-Object { $_.FullName }
python -m pytest $DashboardTests -q
python -m pytest tests/test_dashboard_runtime_evidence_integration_v1.py -q
python scripts/generate_project_manifest.py --check
python scripts/scan_versioned_secrets.py --json
git status --short
git diff --stat
```

## Garantias de segurança

Esta branch preserva:

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
- `changes_model=false`
- `changes_active_signals=false`

## Fora de escopo

- renovar fontes runtime;
- corrigir stale sources;
- executar Docker;
- executar producers;
- alterar Qlib;
- alterar IA Shadow;
- alterar RiskManager;
- executar OCR;
- rebuild de dataset;
- promoção de modelo;
- enviar Telegram/NTFY;
- liberar live, canary ou ordens.
