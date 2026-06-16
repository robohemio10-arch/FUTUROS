
# SMART FUTUROS — Runtime Freshness Governance Closeout Index v1

## Objetivo

Esta branch adiciona um índice institucional read-only para consolidar a cadeia de governança/freshness do dashboard.

O índice liga, em uma única visão:

1. source health autoritativo;
2. runtime evidence/readiness/soak;
3. runbook de remediação;
4. operator pack;
5. closeout evidence;
6. producers externos requeridos;
7. contratos manuais dos producers;
8. static safety dos entrypoints;
9. gate pós-refresh.

## Contrato operacional

O índice não executa producers, não altera runtime e não escreve blockers.

Campos de segurança permanentes:

- `execution_allowed=false`
- `safe_to_execute_from_dashboard=false`
- `paper_only=true`
- `shadow_only=true`
- `live_trading_enabled=false`
- `live_release_allowed=false`
- `canary_release_allowed=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `exchange_private_access=false`
- `sends_orders=false`

## Critério de closeout

`closeout_ready=true` só pode ocorrer quando:

- não houver `global_blocking_reasons`;
- não houver `runtime_evidence_blocking_reasons`;
- não houver `combined_blocking_reasons`;
- o post-refresh gate estiver permitido;
- o closeout evidence estiver permitido;
- todos os estágios da cadeia estiverem OK;
- não houver violação de safety.

## Ações proibidas

- executar producers pelo dashboard;
- alterar snapshots para simular closeout;
- remover blockers manualmente;
- editar YAML/config, risco, modelo, dataset ou sinais;
- habilitar live/canary/orders/private exchange;
- inferir readiness operacional a partir de visibilidade de governança.

## Regra de soak

A visibilidade desta branch não altera a exigência operacional de diagnóstico mínimo de 7 dias e maturidade de 30 dias antes de qualquer discussão futura de canary. Live/canary/orders continuam bloqueados.
