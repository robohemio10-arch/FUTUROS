# Canonical Source of Truth Index

## Finalidade

Este documento define a hierarquia operacional de fontes de verdade do projeto FUTUROS após o fechamento do roadmap técnico/readiness 9/10 e após a incorporação do módulo de notificações críticas NTFY/Telegram.

## Hierarquia canônica

### 1. Repositório Git

A branch `dev` é a fonte primária versionada para:

```text
código-fonte
testes
documentação canônica
scripts de auditoria
manifesto versionado
contratos de segurança
```

Regra: qualquer mudança institucional deve entrar por branch curta, Pull Request, CI/validação local e merge na `dev`.

### 2. Documentos canônicos versionados

Documentos base atuais:

```text
docs/POST_ROADMAP_FINAL_CONSOLIDATION_SNAPSHOT.md
docs/NTFY_TELEGRAM_CRITICAL_NOTIFICATIONS.md
docs/FINANCIAL_EVENT_LOG_AND_ALERTING.md
docs/LIVE_CANARY_CONTRACT_WITH_HARD_BLOCKS.md
docs/MANUAL_GO_NO_GO_LIVE_CANARY_GOVERNANCE.md
docs/SAAS_TENANT_SECURITY_BASELINE.md
```

Documentos externos/históricos anexados ao projeto continuam úteis, mas a versão operacional deve ser consolidada em documentos versionados dentro de `docs/`.

### 3. PROJECT_MANIFEST_CLEAN.json

O arquivo `PROJECT_MANIFEST_CLEAN.json` é a âncora de integridade versionada.

Validação obrigatória:

```powershell
python .\scripts\generate_project_manifest.py --check
```

Se desatualizado:

```powershell
python .\scripts\generate_project_manifest.py --project-root . --output PROJECT_MANIFEST_CLEAN.json
python .\scripts\generate_project_manifest.py --check
```

### 4. Relatórios runtime em data/reports

Relatórios em `data/reports/` são evidência operacional e não devem ser tratados como fonte primária versionada.

Uso correto:

```text
Git/docs/manifesto = verdade institucional
data/reports = evidência runtime/local da execução
```

Relatórios aplicáveis:

```text
data/reports/critical_alerting_report.json
data/reports/critical_notification_dispatch_report.json
data/reports/post_roadmap_final_consolidation_snapshot.json
data/reports/current_project_handover_audit_report.json
```

### 5. Handover técnico atualizado

O handover técnico versionado atual deve ser usado para abrir novo chat e retomar o projeto sem depender da memória conversacional.

Arquivo atual:

```text
docs/CURRENT_PROJECT_HANDOVER_AFTER_NTFY_TELEGRAM.md
```

## Invariantes de segurança

```text
paper_only=true
shadow_only=true
live_trading_enabled=false
live_release_allowed=false
canary_release_allowed=false
order_submission_enabled=false
real_order_submission_enabled=false
exchange_private_access=false
sends_orders=false
changes_risk=false
```

## Próxima pendência antes do dashboard

Antes de iniciar painel Streamlit para notificações críticas, resolver:

```text
codex/zip-standalone-dynamic-import-audit-fix
```

Motivo:

```text
Auditoria ZIP standalone deve rodar sem PYTHONPATH/pacote instalado.
```

Depois disso, a branch correta para dashboard será:

```text
codex/critical-notifications-dashboard-panel
```
