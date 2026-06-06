# Ledger and risk recovery evidence materialization

Esta frente melhora a classificacao institucional de evidencias locais de
ledger e recuperacao de risco sem inventar eventos, sem liberar readiness e sem
tocar em live trading.

## Ledger evidence

O auditor `scripts/run_order_intent_capital_ledger_audit.py` diferencia:

- `repository_missing`: arquivo de ledger nao existe.
- `repository_empty`: arquivo existe, mas esta vazio.
- `repository_present_but_no_events`: schema existe, mas nao ha eventos reais.
- `repository_present_with_valid_events`: schema existe e ha eventos validos.
- `schema_invalid`: tabelas/colunas centrais estao ausentes ou invalidas.
- `event_schema_invalid`: tabelas/colunas de eventos estao ausentes ou invalidas.

Uma estrutura vazia pode ser criada com:

```bash
python scripts/run_order_intent_capital_ledger_audit.py --initialize-empty-repository
```

Isso cria somente o schema paper/shadow em `data/runtime/` quando solicitado. O
arquivo gerado nao deve ser versionado, nao contem eventos falsos e nao conta
como evidencia operacional completa enquanto nao houver ordem paper/shadow real
registrada pelo runtime.

## Risk recovery evidence

O auditor `scripts/run_risk_recovery_mode_audit.py` passa a expor
`evidence_quality_summary`, `missing_sources`, `optional_sources_missing`,
`required_sources_missing` e `next_required_actions`.

As classificacoes principais incluem:

- `missing_runtime_sources`
- `no_incidents_observed`
- `no_drawdown_state`
- `market_health_ok_but_no_recovery_state`
- `recovery_policy_present`
- `recovery_mode_active`
- `recovery_mode_inactive`
- `recovery_state_empty`
- `recovery_state_invalid`

Ter policy presente e zero incidentes observados nao equivale a recuperacao
operacional completa. Para evidencia completa, o runtime precisa ter fontes de
drawdown/trades fechados e relatórios consistentes, sem fontes invalidas e sem
achados bloqueantes.

## Required and optional evidence

Ledger:

- Obrigatorio para evidencia completa: schema valido e eventos reais de
  `order_intents`, `capital_reservations` ou tabelas de eventos.
- Opcional para materializacao local: inicializar schema vazio sem eventos.

Risk recovery:

- Obrigatorio para evidencia completa: fontes runtime suficientes para medir
  drawdown/trades fechados e avaliar modo recomendado.
- Opcional: incidentes, kill switch, backtest, Monte Carlo e reports auxiliares
  continuam enriquecendo diagnostico, mas ausencia isolada nao fabrica blocker
  de live.

## Safety

Todos os relatórios preservam:

```text
paper_only=true
shadow_only=true
live_trading_enabled=false
order_submission_enabled=false
real_order_submission_enabled=false
sends_orders=false
exchange_private_access=false
changes_risk=false
```

Esta branch nao remove `no_trade`, nao altera stake/leverage, nao envia ordens,
nao acessa exchange privada, nao altera Freqtrade DB e nao promove modelo. Mesmo
com diagnostico melhor, readiness e live continuam bloqueados pelos gates
institucionais existentes.
