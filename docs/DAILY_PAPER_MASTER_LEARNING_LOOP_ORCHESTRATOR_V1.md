# DAILY_PAPER_MASTER_LEARNING_LOOP_ORCHESTRATOR_V1

## Objetivo

Esta branch adiciona o orquestrador research-only do Daily Paper/Master Learning Loop do SMART FUTUROS.

O componente consolida as etapas já criadas na trilha Daily Learning em um payload único, determinístico e auditável. A branch não lê fontes reais por padrão, não escreve artefatos de runtime por padrão e não concede autoridade operacional.

## Escopo

O orquestrador modela a sequência canônica:

1. Contracts and source map
2. Read-only loaders
3. Paper/Master KPI pack
4. Divergence and temporal alignment
5. Candle coverage and entry features
6. Mistake and winner catalog
7. Pattern mining research
8. Candidate shadow rule registry
9. Shadow rule OOS validation
10. AI Shadow feedback bridge
11. Qlib research dataset

## Garantias de segurança

- `research_only=true`
- `read_only=true`
- `paper_only=true`
- `shadow_only=true`
- `operational_authority=false`
- `runs_training=false`
- `updates_qlib_runtime=false`
- `updates_ai_shadow_runtime=false`
- `updates_freqtrade=false`
- `updates_risk_manager=false`
- `applies_shadow_rules=false`
- `applies_feedback_to_ai_shadow=false`
- `can_promote_model=false`
- `can_promote_rules=false`
- `writes_data=false`
- `writes_runtime=false`
- `writes_reports=false`
- `live/canary/orders/exchange privada bloqueados`

## CLI

Execução padrão sem escrita:

```powershell
python .\scripts\build_daily_paper_master_learning_loop_orchestrator_v1.py `
  --project-root . `
  --no-write `
  --json
```

O modo padrão não chama os builders das etapas e retorna `input_mode=no_runtime_rows_loaded`. O parâmetro `--execute-stage-builders` existe apenas como integração research-only opcional e permanece desligado por padrão.

## Restrições de output

Mesmo quando `--output` é usado, o CLI bloqueia caminhos sob:

- `data/`
- `runtime/`
- `reports/`
- `logs/`
- `freqtrade/`

## Fora de escopo

- Treinamento Qlib
- Atualização de runtime Qlib
- Atualização de IA Shadow operacional
- Alteração de Freqtrade
- Alteração de RiskManager
- Promoção de modelo
- Promoção/aplicação de regra candidata
- Liberação live/canary
- Escrita de artefatos operacionais em `data/`, `runtime/`, `reports/`, `logs/` ou `freqtrade/`

## Decisão

A decisão canônica permanece:

```text
BLOQUEADO_PARA_OPERACAO_MANTER_EM_RESEARCH
```
