# Monte Carlo Risk Ruin Stress Gate V1

## Objetivo

Este gate calcula uma evidência research-only de risco de ruína via Monte Carlo para candidatos, estratégias e
feedback paper. Ele serve para avaliar robustez financeira antes de qualquer etapa futura de readiness.

O gate é informativo. Ele não altera RiskManager, limites de risco, Freqtrade, runtime, modelos, registry, sinais,
SQLite, parquet ou ordens.

## Entradas read-only

A fonte primária de retornos é:

- `data/reports/financial_label_target_store_v1.json`

O builder lê `target_records` e usa, nessa ordem:

- `target_expected_value_component`
- `target_net_pnl`
- `net_pnl`
- `pnl`

Também pode ler evidências auxiliares, quando existirem:

- `data/reports/ai_qlib_drift_regime_monitor_v1.json`
- `data/reports/paper_autotrain_feedback_loop_v1.json`
- `data/reports/event_driven_backtest_execution_cost_gate_v1.json`

Se não houver retornos válidos, o relatório retorna:

- `status=blocked`
- `reason=no_valid_returns_source`
- `gate_decision=BLOCKED`

## Métricas calculadas

Cada cenário reporta:

- `risk_of_ruin`
- `max_drawdown_p95`
- `max_drawdown_p99`
- `cvar_95`
- `cvar_99`
- `loss_streak_p95`
- `loss_streak_p99`
- `capital_floor_breach_probability`
- `expected_terminal_equity`
- `terminal_equity_p05`
- `terminal_equity_p50`
- `terminal_equity_p95`

## Cenários de stress

O gate roda:

- `baseline`
- `fee_slippage_stress`
- `loss_cluster_stress`
- `fat_tail_stress`
- `low_liquidity_stress`
- `combined_adverse_stress`

O pior cenário governa a decisão agregada.

## Decisão

As decisões possíveis são:

- `PASS`
- `WARNING`
- `BLOCKED`

Mesmo quando o gate passa, ele continua sem autoridade operacional:

- `operational_authority=false`
- `can_change_risk_limits=false`
- `can_stop_bot=false`
- `can_send_orders=false`
- `can_promote_model=false`

## Escrita

O padrão é no-write:

```powershell
python .\scripts\build_monte_carlo_risk_ruin_stress_gate_v1.py --project-root . --json
```

Escrita explícita:

```powershell
python .\scripts\build_monte_carlo_risk_ruin_stress_gate_v1.py --project-root . --write --json
```

Com `--write`, apenas estes arquivos podem ser materializados:

- `data/reports/monte_carlo_risk_ruin_stress_gate_v1.json`
- `data/reports/monte_carlo_risk_ruin_stress_gate_v1.md`

## Fora de escopo

Esta branch não:

- altera Freqtrade;
- altera RiskManager;
- altera `config/risk_limits.yml`;
- muda runtime;
- treina modelo;
- promove modelo;
- escreve registry;
- envia ordens;
- acessa exchange privada;
- escreve SQLite;
- escreve parquet;
- altera modelos.
