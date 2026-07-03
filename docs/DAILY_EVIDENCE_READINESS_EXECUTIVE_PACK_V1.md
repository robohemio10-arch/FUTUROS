# Daily Evidence Readiness Executive Pack V1

## Objetivo

O `daily_evidence_readiness_executive_pack_v1` consolida evidências diárias de readiness em um pacote executivo informativo para revisão humana.

O pacote não é um gate de autorização operacional. Ele não cria scheduler, não treina modelos, não promove modelos, não escreve registry ativo, não altera runtime, não altera Freqtrade/RiskManager, não envia ordens e não acessa exchange privada.

## Fontes de Verdade

O pacote consolida:

- Qlib backend environment lock;
- Qlib backend dependency gate;
- `paper_autotrain_feedback_loop_v1`;
- `daily_learning_evidence_readiness_integration_v1`;
- `ai_qlib_drift_regime_monitor_v1`.

Quando relatórios já existem em `data/reports`, eles são tratados como inputs read-only. O caminho normal também chama builders Python internos em modo no-write para obter uma fotografia atual sem subprocess e sem `shell=True`.

## Fluxo

1. Coleta evidências no-write.
2. Extrai status, decisão, blockers, warnings e hashes.
3. Consolida safety flags.
4. Calcula `status` do pacote:
   - `blocked` se seção crítica estiver bloqueada, readiness estiver bloqueado, drift/regime estiver instável ou fonte obrigatória faltar;
   - `warning` se houver warnings sem blocker;
   - `ok` apenas quando não houver blockers/warnings críticos.
5. Mantém `decision=MANTER_EM_RESEARCH` sempre.

## Comandos

Modo padrão, sem escrita:

```powershell
python .\scripts\build_daily_evidence_readiness_executive_pack_v1.py --project-root . --json
```

Escrita explícita:

```powershell
python .\scripts\build_daily_evidence_readiness_executive_pack_v1.py --project-root . --write-report --json
```

Forçar no-write, mesmo com `--write-report`:

```powershell
python .\scripts\build_daily_evidence_readiness_executive_pack_v1.py --project-root . --write-report --no-write --json
```

## Outputs

Com `--write-report`, somente estes arquivos runtime são materializados:

- `data/reports/daily_evidence_readiness_executive_pack_v1.json`
- `data/reports/daily_evidence_readiness_executive_pack_v1.md`
- `data/reports/daily_evidence_readiness_executive_pack_v1.html`

Esses arquivos não devem ser versionados.

## Markdown

O relatório Markdown contém:

- Executive Summary;
- Release Decision;
- Current Blockers;
- Qlib Backend;
- Paper Autotrain Feedback Loop;
- Daily Learning Readiness;
- AI/Qlib Drift & Regime;
- Safety Invariants;
- Allowed Next Steps;
- Forbidden Actions.

## HTML

O HTML é estático e sem dependência externa. Ele não usa JavaScript remoto, CSS remoto, `http://` ou `https://`.

Ele contém cards de status, tabela de blockers, tabela de safety flags, data/hora UTC e o aviso:

```text
Informational only — no operational authority
```

## Decisões Possíveis

O campo `status` pode ser:

- `blocked`;
- `warning`;
- `ok`.

O campo `decision` permanece sempre:

```text
MANTER_EM_RESEARCH
```

Mesmo quando `status=ok`, o pacote não autoriza live, canary, promoção, scheduler ou alteração operacional.

## Safety Flags

O output preserva:

```text
paper_only=true
shadow_only=true
research_only=true
read_only=true
informational_only=true
operational_authority=false
readiness_release_authority=false
release_allowed=false
live_release_allowed=false
canary_release_allowed=false
order_submission_enabled=false
real_order_submission_enabled=false
sends_orders=false
exchange_private_access=false
changes_risk=false
changes_model=false
model_promotion_performed=false
registry_write_performed=false
active_model_changed=false
qlib_runtime_updated=false
ai_shadow_runtime_updated=false
updates_freqtrade=false
updates_risk_manager=false
creates_scheduler=false
creates_cron=false
creates_systemd_timer=false
creates_windows_task=false
writes_runtime=false
writes_sqlite=false
writes_parquet=false
```

## Limitações

- O pacote resume evidências já existentes; ele não substitui revisão humana.
- Relatórios runtime podem estar ausentes ou stale. Nesses casos, o pack deve explicitar blockers/warnings.
- Qualquer mudança operacional futura exige branch separada, revisão e gates próprios.

## Por Que Não Há Scheduler

Esta branch cria apenas um pacote executivo sob demanda. Criar cron, systemd timer, Windows Task ou serviço persistente mudaria o escopo operacional e poderia introduzir execução automática não revisada.

## Por Que Não Há Promoção Automática

O pacote é informativo. Promoção de modelo, registry ativo, alteração de Qlib/IA Shadow runtime ou qualquer mudança de risco deve ocorrer apenas em fluxo separado e explicitamente autorizado.
