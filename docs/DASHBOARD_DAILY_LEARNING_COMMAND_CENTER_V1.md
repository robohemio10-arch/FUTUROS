# DASHBOARD DAILY LEARNING COMMAND CENTER V1

## Objetivo

Esta branch adiciona uma superfície read-only para o Daily Learning Command Center do SMART FUTUROS.

O objetivo é consolidar, para visualização, o estado dos artefatos research-only criados nas branches anteriores:

- scheduler paper-only;
- orquestrador Daily Paper/Master Learning Loop;
- Qlib research dataset;
- AI Shadow feedback bridge;
- candidate shadow rule registry;
- shadow rule OOS validation.

## Escopo

A branch cria um snapshot determinístico de dashboard com:

- source cards;
- gate matrix;
- source summary;
- command center sections;
- safety footer;
- operator decision sempre bloqueada.

## Arquivos

- `smartcrypto/dashboard/services/daily_learning_command_center.py`
- `smartcrypto/dashboard/pages/daily_learning_command_center.py`
- `scripts/build_dashboard_daily_learning_command_center_v1.py`
- `tests/test_dashboard_daily_learning_command_center_v1.py`
- `docs/DASHBOARD_DAILY_LEARNING_COMMAND_CENTER_V1.md`

## Safety contract

O dashboard permanece sem autoridade operacional:

- `status=blocked`
- `decision=MANTER_EM_RESEARCH`
- `research_only=true`
- `read_only=true`
- `paper_only=true`
- `shadow_only=true`
- `dashboard_readonly=true`
- `operational_authority=false`
- `registers_scheduler=false`
- `executes_orchestrator=false`
- `executes_stage_builders=false`
- `runs_training=false`
- `updates_qlib_runtime=false`
- `updates_ai_shadow_runtime=false`
- `updates_freqtrade=false`
- `updates_risk_manager=false`
- `applies_shadow_rules=false`
- `applies_feedback_to_ai_shadow=false`
- `can_promote_model=false`
- `can_promote_rules=false`

## Proibições explícitas

Esta branch não pode:

- criar botão operacional;
- registrar scheduler real;
- executar scheduler;
- executar orquestrador;
- executar stage builders;
- aplicar regras candidatas;
- aplicar feedback na IA Shadow;
- treinar modelo;
- promover modelo;
- promover regras;
- alterar Freqtrade;
- alterar RiskManager;
- alterar Qlib runtime;
- alterar IA Shadow runtime;
- habilitar live;
- habilitar canary;
- enviar ordens;
- acessar exchange privada;
- escrever em `data/`, `runtime/`, `reports/`, `logs/` ou `freqtrade/`.

## CLI

Comando padrão de auditoria:

```powershell
python .\scripts\build_dashboard_daily_learning_command_center_v1.py `
  --project-root . `
  --no-write `
  --json
```

O modo padrão é no-write. Escrita só é possível com `--output` explícito e fora das árvores bloqueadas.

## Validação

Validações esperadas:

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests\test_dashboard_daily_learning_command_center_v1.py -q
python .\scripts\build_dashboard_daily_learning_command_center_v1.py --project-root . --no-write --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r ".\requirements-dev.lock" --progress-spinner off
git diff --check
git status --short
```

## Decisão

A decisão final permanece:

```text
BLOQUEADO_PARA_OPERACAO_MANTER_EM_RESEARCH
```

O dashboard é uma camada de observabilidade e governança read-only, não uma superfície de execução.
