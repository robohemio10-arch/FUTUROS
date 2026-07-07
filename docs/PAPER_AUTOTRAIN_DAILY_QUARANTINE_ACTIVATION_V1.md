# Paper Autotrain Daily Quarantine Activation V1

## Objetivo

Esta branch ativa a execução diária/manual do autotreinamento em modo
`research-only` e `quarantine-only`. Ela permite gerar evidência, candidatos e
artefatos de modelo em quarentena sem liberar qualquer uso operacional.

## Quarentena versus ativação operacional

Autotreinamento em quarentena significa:

- ler trades paper fechados;
- montar eventos de feedback de pesquisa;
- usar microbatch incremental existente como fonte de treino;
- treinar challengers Qlib/IA Shadow quando os backends estão disponíveis;
- gravar artefatos apenas em `data/research`, `data/models/quarantine` e
  `data/registries/quarantine`.

Ativação operacional continua bloqueada. A branch não escreve sinais ativos,
não altera Freqtrade, não altera RiskManager, não promove modelo e não altera
Qlib/IA Shadow runtime.

## Comando manual one-shot

```powershell
python .\scripts\run_paper_autotrain_daily_quarantine_activation_v1.py --project-root . --once --write-feedback --train-challenger --write-quarantine-artifacts --write-report --json
```

Esse comando executa o ciclo permitido e escreve somente artefatos de
quarentena/research.

## Scheduler

O modo `--scheduler-check` apenas verifica se existe runner seguro e quais
flags seriam usadas por um agendador futuro:

```powershell
python .\scripts\run_paper_autotrain_daily_quarantine_activation_v1.py --project-root . --scheduler-check --json
```

Ele não registra cron, systemd timer, Windows Task, serviço ou container.

## Paths permitidos

- `data/feedback/`
- `data/reports/paper_autotrain_daily_quarantine_activation_v1.json`
- `data/reports/paper_autotrain_daily_quarantine_activation_v1.md`
- `data/research/paper_autotrain_daily_quarantine/`
- `data/models/quarantine/paper_autotrain/`
- `data/registries/quarantine/paper_autotrain_candidate_registry_v1.json`

## Paths proibidos

- `data/runtime/`
- `data/models/active/`
- `data/models/champion/`
- `data/registries/active/`
- `active_freqtrade_signals.json`
- `freqtrade/user_data/`
- `config/`
- `.env`
- SQLite operacional
- Parquet operacional de produção

## Por que não há promoção

Os artefatos são challengers em quarentena. O relatório sempre preserva:

- `promoted_candidate_count=0`
- `active_model_changed=false`
- `model_promotion_performed=false`
- `active_registry_changed=false`
- `runtime_updated=false`

Qualquer promoção futura exige branch separada, registry ativo separado,
readiness/gates e go/no-go manual.

## Como validar artefatos

Depois do one-shot, verifique:

- relatório JSON/Markdown em `data/reports`;
- microbatch snapshot e `last_run_state.json` em `data/research/paper_autotrain_daily_quarantine`;
- modelos JSON em `data/models/quarantine/paper_autotrain/<run_id>/`;
- registry de quarentena em `data/registries/quarantine/paper_autotrain_candidate_registry_v1.json`.

Esses arquivos são runtime/data e não devem ser versionados.

## Critérios futuros para sair da quarentena

Antes de qualquer ativação operacional:

- backends e datasets devem estar estáveis;
- drift/cost/readiness/Monte Carlo devem estar aceitos;
- candidatos devem passar OOS e registry gate;
- paper selector runtime deve ter branch própria;
- RiskManager continua autoridade final;
- live/canary/order real continuam bloqueados até go/no-go manual.
