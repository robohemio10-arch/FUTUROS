# Paper/Master Divergence OOS Slice Metrics V1

## Objetivo

Criar a camada research-only para cálculo de métricas OOS por fatia da divergência Paper vs Master, focada nas hipóteses H1/H2/H6.

A branch não aplica regra, não registra candidate rule, não altera Freqtrade, RiskManager, Qlib runtime, IA Shadow runtime, modelo, risco, scheduler ou qualquer superfície operacional.

## Escopo

Dimensões obrigatórias:

- day
- symbol
- side
- exit_reason
- duration_bucket
- covered_vs_uncovered

Hipóteses cobertas:

- H1: fast stop-loss destrói expectancy.
- H2: ETH long pode ser cluster symbol/side/regime negativo.
- H6: qualidade do filtro/candidate rule precisa de auditoria de false positive, false negative e winner retention.

## Métricas mínimas

- trade_count
- net_pnl
- profit_factor
- win_rate
- max_drawdown
- winner_retention_rate
- winner_pnl_removed
- loser_pnl_removed
- false_positive_count
- false_negative_count
- precision
- recall
- coverage_ratio
- simulated_removed_pnl_delta

## Contrato de segurança

- research_only=true
- read_only=true
- paper_only=true
- shadow_only=true
- operational_authority=false
- can_apply_to_freqtrade=false
- can_apply_to_risk_manager=false
- can_promote_rules=false
- can_promote_model=false
- updates_freqtrade=false
- updates_risk_manager=false
- updates_qlib_runtime=false
- updates_ai_shadow_runtime=false
- sends_orders=false
- live_release_allowed=false
- canary_release_allowed=false

## Decisão esperada

Mesmo quando linhas explícitas forem carregadas para cálculo de métricas, a decisão permanece:

```text
status=blocked
decision=MANTER_EM_RESEARCH
oos_validation_required=true
oos_validated=false
ready_for_candidate_registry=false
remediation_application_allowed=false
```

## Próximo passo

Após esta branch, a próxima frente deve conectar uma fonte real read-only de trades/cobertura para alimentar `--input-json`, sem versionar runtime/data e sem promover qualquer regra.
