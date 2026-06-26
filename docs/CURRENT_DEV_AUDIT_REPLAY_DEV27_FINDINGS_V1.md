# CURRENT_DEV_AUDIT_REPLAY_DEV27_FINDINGS_V1

## Objetivo

Auditoria retroativa, **audit-only**, dos achados DEV27 contra a `dev` atual do SMART FUTUROS.

Esta branch responde se ainda existem sinais atuais de:

1. risco de execução CLI standalone sem bootstrap explícito;
2. risco de notificação externa real a partir do dashboard/Streamlit;
3. presença de artefatos de runtime safety strict;
4. necessidade ou não da branch `codex/cli-standalone-bootstrap-and-dashboard-notification-hardening-v1`.

## Natureza da branch

Esta branch não corrige arquivos. Ela apenas audita e produz uma decisão explícita:

```text
branch_00b_required=true|false
```

## Invariantes

```text
research_only=true
read_only=true
paper_only=true
shadow_only=true
operational_authority=false
release_authority=false
fixes_applied=false
changes_runtime=false
changes_risk=false
changes_model=false
sends_orders=false
live_release_allowed=false
canary_release_allowed=false
```

## Escopo

Incluído:

- scan estático de scripts em `scripts/` para risco standalone;
- scan estático de dashboard em `smartcrypto/dashboard/*` para primitivas de notificação externa real;
- inventário estático de artefatos de runtime safety;
- decisão auditável sobre necessidade da branch 00B.

Fora de escopo:

- corrigir bootstrap em scripts;
- alterar dashboard;
- alterar Freqtrade;
- alterar RiskManager;
- alterar Qlib;
- alterar IA Shadow runtime;
- treinar modelo;
- promover modelo;
- promover regra;
- registrar scheduler;
- enviar Telegram/NTFY;
- usar exchange privada;
- escrever em `data/`, `runtime/`, `reports/`, `logs/` ou `freqtrade/`.

## Execução

```powershell
python .\scripts\audit_current_dev_dev27_findings_replay_v1.py `
  --project-root . `
  --no-write `
  --json
```

## Interpretação

| Resultado | Interpretação |
|---|---|
| `status=ok` e `branch_00b_required=false` | Achados DEV27 não exigem hardening adicional nesta frente. |
| `status=warning` | Há sinais para revisão manual, mas sem bloqueio crítico automático. |
| `status=blocked` ou `branch_00b_required=true` | Abrir branch 00B cirúrgica para hardening confirmado. |

## Gates mínimos

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests\test_current_dev_audit_replay_dev27_findings_v1.py -q
python .\scripts\audit_current_dev_dev27_findings_replay_v1.py --project-root . --no-write --json
python .\scripts\generate_project_manifest.py
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r ".\requirements-dev.lock" --progress-spinner off
git diff --check
git status -sb
git status --short
```
