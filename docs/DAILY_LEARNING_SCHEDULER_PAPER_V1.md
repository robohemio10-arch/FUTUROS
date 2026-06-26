# DAILY_LEARNING_SCHEDULER_PAPER_V1

## Decisão

`MANTER_EM_RESEARCH`.

A Branch 14 adiciona somente um contrato de scheduler paper-only/research-only para o Daily Learning Loop. Ela não registra scheduler real, não cria cron, não cria systemd timer, não cria Windows Task Scheduler, não cria serviço, não executa o orquestrador automaticamente e não escreve artefatos de runtime por padrão.

## Arquivos

- `smartcrypto/research/daily_learning_scheduler_paper.py`
- `scripts/build_daily_learning_scheduler_paper_v1.py`
- `tests/test_daily_learning_scheduler_paper_v1.py`
- `docs/DAILY_LEARNING_SCHEDULER_PAPER_V1.md`

## Escopo

O pacote cria:

- contrato determinístico de agendamento diário em UTC;
- comando seguro para revisão manual do orquestrador da Branch 13;
- plano de execução não executado;
- preflight checks documentados;
- payload consolidado com hard blocks explícitos.

O comando de orquestrador gerado inclui `--no-write` e `--json` por construção.

## Fora de escopo

A branch não faz:

- registro real de cron;
- registro real de systemd timer;
- registro real de Windows Task Scheduler;
- criação de serviço;
- execução automática do Daily Learning Loop;
- execução dos stage builders;
- leitura real de fontes runtime por padrão;
- escrita em `data/`, `runtime/`, `reports/`, `logs/` ou `freqtrade/`;
- treino de modelo;
- promoção de modelo;
- promoção de regra candidata;
- aplicação de feedback na IA Shadow;
- atualização de Qlib runtime;
- atualização de IA Shadow runtime;
- alteração de Freqtrade;
- alteração de RiskManager;
- envio de ordens;
- uso de exchange privada;
- liberação de live ou canary.

## CLI

No-write padrão recomendado:

```powershell
python .\scripts\build_daily_learning_scheduler_paper_v1.py `
  --project-root . `
  --no-write `
  --json
```

O CLI permite `--output` apenas para arquivo explícito fora de diretórios operacionais. Caminhos sob `data/`, `runtime/`, `reports/`, `logs/` e `freqtrade/` são bloqueados.

## Flags esperadas

- `status=blocked`
- `decision=MANTER_EM_RESEARCH`
- `research_only=true`
- `read_only=true`
- `paper_only=true`
- `shadow_only=true`
- `operational_authority=false`
- `registers_scheduler=false`
- `creates_cron=false`
- `creates_systemd_timer=false`
- `creates_windows_task=false`
- `creates_service=false`
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

## Validação

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests\test_daily_learning_scheduler_paper_v1.py -q
python .\scripts\build_daily_learning_scheduler_paper_v1.py --project-root . --no-write --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r ".\requirements-dev.lock" --progress-spinner off
git diff --check
git status --short
```

## Governança

Este scheduler é contrato de pesquisa. Ele não é evidência de readiness, não libera canary, não libera live e não reduz os gates de 30 dias sem gaps, revisão manual, contrato runtime, evidência OOS e go/no-go operacional.
