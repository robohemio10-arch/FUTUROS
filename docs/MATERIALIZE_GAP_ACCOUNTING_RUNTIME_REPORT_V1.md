# SMART FUTUROS — Materialização do Gap Accounting Runtime Report v1

## Objetivo

Materializar o contrato runtime `data/reports/paper_shadow_soak_gap_accounting_report.json` durante a geração oficial do `runtime_evidence_pack_v2` e do `readiness_snapshot_v2`, sem liberar readiness, canary, live trading ou qualquer execução real.

## Contrato operacional

O auditor `audit_paper_shadow_soak_continuity_and_gap_accounting.py` continua read-only por padrão. A escrita só ocorre quando `--write` é informado diretamente no auditor ou quando o builder institucional `build_runtime_evidence_pack_and_readiness_snapshot_v2.py` é executado em modo de escrita, isto é, sem `--no-write`.

O arquivo materializado é:

```text
data/reports/paper_shadow_soak_gap_accounting_report.json
```

Esse arquivo é runtime artifact e permanece fora do versionamento.

## Semântica de escrita

| Execução | Gap report | Runtime evidence | Readiness snapshot |
|---|---|---|---|
| `build_runtime_evidence_pack_and_readiness_snapshot_v2.py --no-write` | não escreve | não escreve | não escreve |
| `build_runtime_evidence_pack_and_readiness_snapshot_v2.py` | escreve | escreve | escreve |
| `audit_paper_shadow_soak_continuity_and_gap_accounting.py` | não escreve | n/a | n/a |
| `audit_paper_shadow_soak_continuity_and_gap_accounting.py --write` | escreve | n/a | n/a |

## Campos propagados

O runtime evidence/readiness passa a expor o caminho e a materialização do report:

```json
{
  "paper_shadow_soak_gap_accounting_report": "data/reports/paper_shadow_soak_gap_accounting_report.json",
  "write_performed": true,
  "report_materialized": true
}
```

A CLI do runtime evidence também expõe:

```json
{
  "paper_shadow_soak_gap_accounting_report_path": ".../data/reports/paper_shadow_soak_gap_accounting_report.json",
  "paper_shadow_soak_gap_accounting_report_write_performed": true,
  "paper_shadow_soak_gap_accounting_report_materialized": true
}
```

## Dashboard

Após uma execução com escrita do runtime evidence, os builders de snapshots passam a consumir o report materializado em:

- Active Controls
- Quantitative Reports

Isso remove o `missing_optional_sources` relacionado ao gap accounting quando o relatório já foi materializado.

## Segurança

Esta branch não altera nenhum caminho de execução real:

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
- `runs_ocr=false`
- `writes_trades_master=false`

O status pode e deve continuar `blocked` enquanto houver gaps críticos, soak inferior a 30 dias ou fontes de readiness bloqueadas.

## Validação

Comandos esperados:

```powershell
python -m compileall scripts smartcrypto tests

python -m pytest tests/test_materialize_gap_accounting_runtime_report_v1.py tests/test_runtime_evidence_gap_accounting_integration_v1.py tests/test_runtime_evidence_pack_and_readiness_snapshot_v2.py tests/test_dashboard_active_controls_snapshot_builder_v2.py tests/test_dashboard_quantitative_reports_snapshot_builder_v2.py tests/test_paper_shadow_soak_gap_accounting_script_v1.py -q

python scripts/build_runtime_evidence_pack_and_readiness_snapshot_v2.py --project-root . --no-write --json
python scripts/build_runtime_evidence_pack_and_readiness_snapshot_v2.py --project-root . --json
python scripts/build_dashboard_snapshots.py --project-root . --once --strict false --output-dir data/reports --json
python scripts/generate_project_manifest.py --check
python scripts/scan_versioned_secrets.py --json
```

Resultado institucional esperado: runtime/dashboard podem permanecer `blocked` ou `degraded`, mas sem liberação de canary/live e sem fonte opcional ausente para o gap accounting após a execução com escrita.
