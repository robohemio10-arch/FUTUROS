# SaaS Tenant Security Baseline

Esta branch define baseline read-only de segurança SaaS/multi-tenant. Ela não ativa SaaS real, não cria tenants reais, não altera risco, não acessa exchange privada e não envia ordens.

## Arquivos

- `smartcrypto/ops/saas_tenant_security_baseline.py`
- `scripts/audit_saas_tenant_security_baseline.py`
- `tests/test_saas_tenant_security_baseline.py`
- `data/reports/saas_tenant_security_baseline.json` quando a CLI roda com escrita habilitada

## Escopo

O baseline cobre:

```text
tenant isolation
RBAC baseline
secret hygiene
audit trail
admin/read-only separation
runtime/data boundary
no cross-tenant leakage
paper/shadow only
```

## Invariantes

```text
paper_only=true
shadow_only=true
live_release_allowed=false
canary_release_allowed=false
sends_orders=false
changes_risk=false
exchange_private_access=false
tenant_runtime_mutation_allowed=false
cross_tenant_data_access_allowed=false
```

## Uso

Sem escrita:

```powershell
python .\scripts\audit_saas_tenant_security_baseline.py --no-write --json
```

Com escrita padrão:

```powershell
python .\scripts\audit_saas_tenant_security_baseline.py
```

## Arquivos opcionais de política

O baseline aceita, mas não exige, os arquivos:

```text
config/saas/tenant_registry.json
config/saas/access_policy.json
```

Ausência desses arquivos gera warning, não bloqueio, pois esta branch apenas cria baseline institucional. Se forem criados, passam a ser validados rigidamente.

## Exemplo de tenant registry

```json
{
  "tenants": [
    {
      "tenant_id": "tenant_demo",
      "display_name": "Tenant Demo",
      "environment": "paper",
      "data_namespace": "tenant_demo_data",
      "runtime_namespace": "tenant_demo_runtime",
      "runtime_mutation_allowed": false,
      "cross_tenant_data_access_allowed": false,
      "exchange_private_access": false
    }
  ]
}
```

## Exemplo de access policy

```json
{
  "tenant_isolation_required": true,
  "rbac_required": true,
  "audit_trail_required": true,
  "admin_read_only_separation_required": true,
  "secret_hygiene_required": true,
  "runtime_data_boundary_required": true,
  "cross_tenant_leakage_prevention_required": true,
  "paper_shadow_only_required": true,
  "tenant_runtime_mutation_allowed": false,
  "cross_tenant_data_access_allowed": false,
  "shared_runtime_namespace_allowed": false,
  "shared_data_namespace_allowed": false,
  "plaintext_secret_allowed": false,
  "live_trading_enabled": false,
  "exchange_private_access": false,
  "order_submission_enabled": false,
  "real_order_submission_enabled": false,
  "sends_orders": false,
  "changes_risk": false,
  "live_release_allowed": false,
  "canary_release_allowed": false,
  "roles": {
    "viewer": {"permissions": ["read_reports"]},
    "operator": {"permissions": ["read_reports", "run_read_only_audits"]},
    "admin": {"permissions": ["read_reports", "run_read_only_audits", "manage_users"]}
  }
}
```

## Fora de escopo

Esta branch não implementa billing, autenticação real, provisionamento de tenant, deploy multi-tenant ou execução real. Ela apenas materializa baseline de segurança auditável para evolução posterior.
