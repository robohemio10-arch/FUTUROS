# Current Project Handover After NTFY/Telegram

## Estado do projeto

Projeto: FUTUROS / SmartCrypto  
Raiz operacional padrão: `E:\FUTUROS`  
Branch canônica: `dev`  
Estado operacional: paper/shadow only  
Live/canary: bloqueados por contrato

## Último marco mergeado

```text
PR #125 - Adiciona notificações críticas NTFY Telegram
merge commit: e18c6a1cbdcba9e864ed53cc0f55ee1f5f923e3b
```

## Validação final conhecida após PR #125

```text
pytest: 1138 passed, 40 warnings
PROJECT_MANIFEST_CLEAN.json: manifest_current
scan_versioned_secrets.py: status ok
paper_only: true
shadow_only: true
sends_orders: false
exchange_private_access: false
git status: clean
```

O erro recorrente `PermissionError pytest-current` no Windows ocorre após a suíte passar e é ruído de cleanup `atexit` do pytest, não falha dos testes.

## Roadmap técnico/readiness 9/10 fechado

Frentes concluídas:

```text
1. canonical-30d-soak-readiness-threshold-enforcement
2. transitive-lock-docker-runtime-reproducibility
3. zip-standalone-audit-fallback
4. runtime-evidence-pack-and-readiness-snapshot-v2
5. paper-shadow-soak-continuity-and-gap-accounting
6. monte-carlo-no-trade-recovery-diagnostics
7. ai-shadow-threshold-live-readiness-evidence
8. manual-go-no-go-live-canary-governance
9. live-canary-contract-with-hard-blocks
10. saas-tenant-security-baseline
11. post-roadmap-final-consolidation-snapshot
12. ntfy-telegram-critical-notifications
```

## PRs recentes de referência

```text
#120 ai-shadow-threshold-live-readiness-evidence
#121 manual-go-no-go-live-canary-governance
#122 live-canary-contract-with-hard-blocks
#123 saas-tenant-security-baseline
#124 post-roadmap-final-consolidation-snapshot
#125 ntfy-telegram-critical-notifications
```

## Estado do módulo NTFY/Telegram

Disponível na `dev`:

```text
smartcrypto/ops/notification_channels.py
scripts/run_critical_notification_dispatch.py
config/critical_notifications.example.yml
docs/NTFY_TELEGRAM_CRITICAL_NOTIFICATIONS.md
tests/test_notification_channels.py
```

Funções institucionais:

```text
NtfyNotifier
TelegramNotifier
NotificationDispatcher
dispatch dry-run
relatório de dispatch
config por .env sem segredos versionados
```

Invariantes preservadas:

```text
paper_only=true
runtime_mode=paper
live_trading_enabled=false
order_submission_enabled=false
real_order_submission_enabled=false
exchange_private_access=false
sends_orders=false
changes_risk=false
```

## Estado do dashboard

O módulo NTFY/Telegram ainda não está no dashboard.

Não existe ainda:

```text
painel Streamlit de status NTFY/Telegram
card de canais enabled/configured
visualização de critical_notification_dispatch_report.json
botão de dry-run no dashboard
status de último erro/última tentativa por canal
```

Primeira versão recomendada do dashboard:

```text
read-only
sem envio real
dry-run opcional
sem exibir token/topic/chat_id
sem alterar risco/runtime
```

## Pendência P1 antes do dashboard

A auditoria 9/10 recebida sobre ZIP anterior indicou risco P1:

```text
ZIP standalone audit fallback pode falhar sem PYTHONPATH/pacote instalado.
```

Branch prioritária antes do dashboard:

```text
codex/zip-standalone-dynamic-import-audit-fix
```

Escopo:

```text
corrigir import dinâmico standalone em scripts de auditoria
validar generate_project_manifest.py sem PYTHONPATH
validar scan_versioned_secrets.py sem PYTHONPATH
adicionar teste de cópia ZIP/standalone
não alterar runtime/trading/dashboard
preservar sends_orders=false e changes_risk=false
```

Após merge dessa P1, abrir:

```text
codex/critical-notifications-dashboard-panel
```

## Fonte de verdade daqui para frente

Use esta ordem:

```text
1. repositório Git / branch dev
2. docs canônicos versionados
3. PROJECT_MANIFEST_CLEAN.json
4. relatórios data/reports quando aplicável
5. handover técnico atualizado
```

## Comandos de retomada em novo chat

```powershell
cd "E:\FUTUROS"

git fetch origin --prune
git switch dev
git pull --ff-only origin dev
git status --short
git log --oneline -12

python -m pytest -q
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --json
```

## Regras absolutas

```text
não executar ordens reais
não ativar live
não ativar canary automaticamente
não mudar risco sem branch própria e aprovação
não versionar segredos
não usar dashboard como fonte primária
não tratar data/reports como fonte institucional primária
```
