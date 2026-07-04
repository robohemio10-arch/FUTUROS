# Event-Driven Backtest Execution Cost Gate V1

## Objetivo

Este gate avalia, em modo research-only/read-only, se evidências de Qlib, IA Shadow e paper autotrain continuam
economicamente aceitáveis após custos conservadores de execução.

Ele não tem autoridade operacional e não libera live, canary, modelo, regra, registry, RiskManager, Freqtrade ou
signal producer.

## Fontes read-only

O relatório consome apenas artefatos existentes:

- `data/reports/qlib_institutional_ranking_trainer_v1.json`
- `data/reports/ai_shadow_quality_veto_trainer_v1.json`
- `data/reports/walkforward_anti_leakage_split_engine_v1.json`
- `data/reports/walkforward_baseline_summary_v1.json`
- `data/reports/financial_label_target_store_v1.json`
- `data/reports/ai_qlib_drift_regime_monitor_v1.json`
- `data/reports/paper_autotrain_feedback_loop_v1.json`

Se uma fonte requerida estiver ausente ou inválida, o status retorna `blocked` com blocker explícito.

## Modelo de custos

O custo é versionado no relatório e não altera configuração operacional:

- `maker_fee_bps`
- `taker_fee_bps`
- `slippage_bps`
- `spread_bps`
- `funding_bps_per_position`
- `round_trip_cost_bps`
- `execution_cost_model_version=conservative_bps_v1`

Quando funding não está disponível, o gate registra warning conservador e usa funding zero. Isso não transforma
resultado bloqueado em aprovado.

## Métricas calculadas

Para cada split disponível:

- `gross_expected_value`
- `estimated_execution_cost`
- `net_expected_value`
- `net_expected_value_delta`
- `cost_drag_ratio`
- `cost_gate_passed`

Também são calculadas comparações contra baselines quando disponíveis:

- `no_trade`
- `random`
- `always_long`
- `always_short`

Quando o target store contém registros, o gate gera visões por símbolo e lado.

## Critérios de bloqueio

O gate bloqueia quando:

- fonte requerida está ausente;
- não há métricas de split;
- `net_expected_value <= 0`;
- `cost_drag_ratio` excede o limite conservador;
- melhor net EV não supera no-trade.

Mesmo quando um cenário passa, o relatório continua apenas evidência de pesquisa:

- `decision=MANTER_EM_RESEARCH`
- `release_allowed=false`
- `operational_authority=false`

## Comandos

Modo padrão, sem escrita:

```powershell
python .\scripts\build_event_driven_backtest_execution_cost_gate_v1.py --project-root . --json
```

Escrever apenas JSON/Markdown em `data/reports`:

```powershell
python .\scripts\build_event_driven_backtest_execution_cost_gate_v1.py --project-root . --write-report --json
```

`--no-write` prevalece sobre `--write-report`.

## Garantias de segurança

Este gate não:

- treina modelo;
- promove modelo;
- escreve registry;
- altera Qlib runtime;
- altera IA Shadow runtime;
- altera Freqtrade;
- altera RiskManager;
- envia ordens;
- acessa exchange privada;
- escreve SQLite;
- escreve parquet;
- escreve runtime artifact fora dos relatórios explicitamente solicitados.
