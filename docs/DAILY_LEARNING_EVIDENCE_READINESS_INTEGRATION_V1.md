# Daily Learning Evidence Readiness Integration V1

## Escopo

Esta branch cria uma integração **research-only/read-only** entre o Daily Learning Loop e a camada de evidence/readiness do SMART FUTUROS.

O artefato produzido é um snapshot informativo e bloqueado. Ele consolida sinais de scheduler, dashboard command center, orquestrador, Qlib research dataset, feedback bridge, registry de regras candidatas e validação OOS apenas como evidência de governança.

## Decisão institucional

A decisão permanece:

```text
MANTER_EM_RESEARCH
```

O snapshot de readiness permanece:

```text
status=blocked
readiness_status=blocked
readiness_release_authority=false
```

## Garantias negativas

Esta branch não:

- libera live;
- libera canary;
- promove modelo;
- promove regra candidata;
- aplica candidate rule;
- aplica feedback na IA Shadow;
- registra scheduler real;
- executa scheduler;
- executa orquestrador;
- executa stage builders;
- treina modelo;
- altera Qlib runtime;
- altera IA Shadow runtime;
- altera Freqtrade;
- altera RiskManager;
- envia ordem;
- usa exchange privada;
- escreve em `data/`, `runtime/`, `reports/`, `logs/` ou `freqtrade/` por padrão.

## Fontes aceitas

A integração reconhece, em memória, estes payloads:

1. Daily Learning scheduler paper-only.
2. Dashboard Daily Learning Command Center.
3. Daily Paper/Master Learning Loop orchestrator.
4. Qlib research dataset.
5. AI Shadow feedback bridge.
6. Candidate shadow rule registry.
7. Shadow rule OOS validation.

Quando nenhum payload é fornecido, o snapshot renderiza modo vazio seguro:

```text
input_mode=no_runtime_rows_loaded
```

## Gates

A matriz de gates mantém como críticos:

- Daily Learning é evidência informativa apenas.
- Readiness permanece bloqueado.
- Live/canary permanecem bloqueados.
- Nenhuma execução runtime ocorre.
- Nenhuma promoção/aplicação ocorre.

O gate de presença de payload é apenas `info`, portanto a ausência de fontes runtime não bloqueia a validação em modo no-runtime.

## CLI

Comando de validação:

```powershell
python .\scripts\build_daily_learning_evidence_readiness_integration_v1.py `
  --project-root . `
  --no-write `
  --json
```

O CLI não escreve por padrão. Escrita só ocorre com `--output` explícito e fora de árvores proibidas.

## Safety flags obrigatórias

Campos principais esperados:

```text
status=blocked
decision=MANTER_EM_RESEARCH
research_only=true
read_only=true
paper_only=true
shadow_only=true
daily_learning_evidence_is_informational=true
readiness_snapshot_blocked=true
readiness_release_authority=false
operational_authority=false
live_release_allowed=false
canary_release_allowed=false
order_submission_enabled=false
real_order_submission_enabled=false
registers_scheduler=false
executes_scheduler=false
executes_orchestrator=false
executes_stage_builders=false
runs_training=false
updates_qlib_runtime=false
updates_ai_shadow_runtime=false
updates_freqtrade=false
updates_risk_manager=false
applies_shadow_rules=false
applies_feedback_to_ai_shadow=false
can_promote_model=false
can_promote_rules=false
write_performed=false
```
