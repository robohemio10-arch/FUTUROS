# SMART FUTUROS — Paper/Shadow Soak Anchor and Continuity Pack

## Objetivo

Esta entrega adiciona uma camada read-only para ancorar a continuidade do soak paper/shadow após o fechamento do SMART FUTUROS Command Center.

A auditoria consolida evidências existentes e separa explicitamente:

- 7 dias como marco diagnóstico operacional.
- 30 dias como requisito mínimo de readiness.
- ausência de gaps críticos como condição necessária, mas não suficiente.
- go/no-go manual como obrigatório.
- canary/live como bloqueados nesta fase.

## Arquivos adicionados

- `smartcrypto/ops/paper_shadow_soak_anchor/`
- `scripts/audit_paper_shadow_soak_anchor_continuity_pack.py`
- `tests/test_paper_shadow_soak_anchor_contracts_v1.py`
- `tests/test_paper_shadow_soak_anchor_auditor_v1.py`
- `tests/test_paper_shadow_soak_anchor_script_v1.py`
- `tests/test_paper_shadow_soak_anchor_static_safety_v1.py`

## Evidências consumidas

A auditoria lê, quando presentes:

- `data/reports/paper_shadow_soak_report.json`
- `data/reports/paper_shadow_soak_continuity_audit.json`
- `data/reports/runtime_evidence_pack_v2.json`
- `data/reports/readiness_snapshot_v2.json`
- `data/reports/paper_soak_report.json`
- `data/reports/freqtrade_paper_db_authority_report.json`
- `data/reports/monte_carlo_risk_simulation_report.json`
- `scripts/audit_dashboard_semantic_coverage_v2.py`
- `docs/SMART_FUTUROS_DASHBOARD_SEMANTIC_COVERAGE_AUDIT_V2.md`

## Saída

Por padrão, a CLI é read-only e não escreve arquivo:

```powershell
python scripts/audit_paper_shadow_soak_anchor_continuity_pack.py --project-root . --json
```

Para materializar evidência local não versionada em `data/reports`:

```powershell
python scripts/audit_paper_shadow_soak_anchor_continuity_pack.py --project-root . --write --json
```

O arquivo materializado, quando solicitado explicitamente, é:

```text
data/reports/paper_shadow_soak_anchor_continuity_pack.json
```

Esse arquivo é runtime e não deve ser versionado.

## Segurança operacional

Esta branch não executa ordens, não acessa exchange privada, não chama CommandBus real, não envia Telegram/NTFY, não altera risco, não altera modelo, não executa OCR, não importa trades, não reconstrói datasets, não limpa SQLite, não altera readiness e não libera canary/live.

Flags fixas do relatório:

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
  "changes_model": false,
  "changes_config": false,
  "changes_readiness": false,
  "live_release_allowed": false,
  "canary_release_allowed": false,
  "manual_go_no_go_required": true
}
```

## Status esperados

- `evidence_missing`: não há evidência mínima de soak/continuity.
- `blocked`: há evidência, mas 30 dias não foram alcançados, há gap crítico ou violação de segurança.
- `degraded`: não há bloqueio crítico, mas existem avisos/evidências de readiness ausentes.
- `ok`: continuidade ancorada, 30 dias alcançados e sem bloqueio detectado.

Mesmo em `ok`, o relatório mantém:

- `live_release_allowed=false`
- `canary_release_allowed=false`
- `manual_go_no_go_required=true`

## Validação

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests/test_paper_shadow_soak_anchor_contracts_v1.py tests/test_paper_shadow_soak_anchor_auditor_v1.py tests/test_paper_shadow_soak_anchor_script_v1.py tests/test_paper_shadow_soak_anchor_static_safety_v1.py -q
python scripts/audit_paper_shadow_soak_anchor_continuity_pack.py --project-root . --json
python scripts/generate_project_manifest.py --check
python scripts/scan_versioned_secrets.py --json
git ls-files | Select-String "\.(parquet|sqlite|sqlite3|db|csv|xlsx|jsonl|zip)$"
```

## Próxima etapa

Após esta branch, a continuidade paper/shadow fica ancorada em relatório auditável. A etapa seguinte deve continuar bloqueando live/canary e avançar apenas em readiness/evidence pack sem efeitos operacionais.
