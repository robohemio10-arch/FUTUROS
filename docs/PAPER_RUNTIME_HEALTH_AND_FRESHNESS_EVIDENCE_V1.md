# Paper Runtime Health and Freshness Evidence v1

## Objetivo

Esta branch adiciona uma evidência runtime read-only para responder de forma institucional se o paper runtime está vivo, fresco e seguro:

```text
DATA/REPORT: data/reports/paper_runtime_health_and_freshness_report.json
SCHEMA: paper_runtime_health_and_freshness_v1
```

O relatório consolida a saúde dos serviços paper, a frescura dos relatórios operacionais, o catálogo esperado do `docker-compose.paper.yml` e as flags de segurança obrigatórias.

## Escopo

Incluído:

- auditor read-only `smartcrypto.ops.paper_runtime_health_and_freshness`
- CLI `scripts/audit_paper_runtime_health_and_freshness.py`
- integração informativa ao `runtime_evidence_pack_v2` e `readiness_snapshot_v2`
- materialização automática pelo runtime evidence builder quando `--no-write` não é usado
- consumo pelos snapshots de Infraestrutura e Active Controls
- testes de auditor, CLI, runtime evidence, dashboard e segurança estática

Fora de escopo:

- envio de ordens
- acesso privado à exchange
- mudança de risco/modelo/dataset/OCR
- alteração de compose/Docker
- liberação de readiness/canary/live

## Contrato do relatório

Campos principais:

```json
{
  "schema_version": "paper_runtime_health_and_freshness_v1",
  "status": "ok|degraded|blocked",
  "paper_runtime_alive": true,
  "paper_runtime_fresh": true,
  "paper_runtime_health_status": "ok|degraded|blocked",
  "paper_runtime_freshness_status": "fresh|stale_or_missing",
  "critical_stale_count": 0,
  "warning_stale_count": 0,
  "missing_required_sources": [],
  "stale_required_sources": [],
  "stale_optional_sources": [],
  "runtime_reports": [],
  "component_rollup": {},
  "compose_service_catalog": {},
  "container_snapshot": {},
  "paper_only": true,
  "shadow_only": true,
  "live_trading_enabled": false,
  "order_submission_enabled": false,
  "real_order_submission_enabled": false,
  "exchange_private_access": false,
  "sends_orders": false
}
```

## Status

- `ok`: fontes requeridas existem, estão frescas, válidas e sem flags inseguras.
- `degraded`: apenas fontes opcionais stales, catálogo compose degradado ou container snapshot indisponível/degradado quando solicitado.
- `blocked`: fonte requerida ausente/stale/inválida, flag insegura, container unhealthy ou componente crítico.

## Uso

Read-only, sem materializar arquivo:

```powershell
python scripts/audit_paper_runtime_health_and_freshness.py --project-root . --json
```

Com escrita runtime local não versionada:

```powershell
python scripts/audit_paper_runtime_health_and_freshness.py --project-root . --write --json
```

Integração via runtime evidence:

```powershell
python scripts/build_runtime_evidence_pack_and_readiness_snapshot_v2.py --project-root . --json
```

## Segurança

O auditor não usa `ccxt`, não acessa exchange privada, não envia notificações, não chama CommandBus e não altera risco/modelo/config/dataset. A coleta de container via `docker ps` é opcional, read-only e desabilitada por default.
