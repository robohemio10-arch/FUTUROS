# Paper Capacity Scaleout V1

## Estado da Branch 5

Esta branch implementa somente simulação de capacidade adicional:

```text
SIMULATION_MODE=RESEARCH_SIMULATION_ONLY
CAPACITY_ACTIVATION_ALLOWED=false
```

A Branch 4 pode ter `software_dod=PASS` e, simultaneamente,
`financial_evidence=EVIDENCE_BLOCKED`. Isso não autoriza scaleout.

## Cenários

- **C0**: capacidade baseline observada.
- **C1**: `+1` slot global simulado, sem bypass de pair occupancy.
- **C2**: allocation conservador, exigindo EV positivo e regime explícito.
- **C3**: stress de custo/perdas e Monte Carlo marginal.
- **C4**: kill-switch/stale-data fail-closed.

## Contrato de identidade

A Branch 5 não reconstrói lineage histórico.

São proibidos:

```text
fuzzy_matching
timestamp_nearest_matching
historical_backfill
trade_id_as_candidate_id
symbol_side_join_as_identity
candidate_ev_as_realized_pnl
```

## Contrato de outcome marginal

Para avaliar financeiramente um recovery de capacidade é necessária uma fonte
explícita com:

```text
candidate_id
outcome_available_at_utc
realized_net_pnl_usdt
```

`effective_arm_pnl_usdt` é aceito somente quando representa explicitamente o
outcome líquido da mesma observação.

O arquivo:

```text
data/reports/paper_ab_edge_selector_assignments_v1.jsonl
```

é um ledger de assignments, não uma fonte de outcomes realizados. Portanto ele
não é usado como default da Branch 5 e não pode gerar PnL marginal.

Se não existir overlap exato `candidate_id -> realized outcome`, o resultado
correto é:

```text
CAPACITY_EVIDENCE=INSUFFICIENT
DECISION=AGUARDAR_EVIDENCIA
CAPACITY_ACTIVATION_ALLOWED=false
```

## Evidência observada em 22/08/2026

A auditoria read-only encontrou:

```text
candidate_id unique values = 0
signal_id unique values = 0
decision_event_id unique values = 0
trade_id unique values = 777
```

O research producer possui `source_candidate_id` e `signal_candidate_id`, mas
esses identificadores não formam uma cadeia exata observável até os outcomes
Paper atuais.

Isso é compatível com o contrato prospectivo de lineage: software implementado
não equivale a evidência runtime já materializada.

## Segurança

Sempre:

```text
changes_max_open_trades=false
changes_risk=false
changes_strategy=false
changes_model=false
writes_runtime=false
writes_sqlite=false
sends_orders=false
live_release_allowed=false
canary_release_allowed=false
```

Persistência opcional continua restrita a `data/reports` e `data/research` com
flags explícitas. O modo default é no-write.
