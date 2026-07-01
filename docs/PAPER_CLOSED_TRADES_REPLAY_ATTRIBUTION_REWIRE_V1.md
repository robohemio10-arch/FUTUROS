# Paper Closed Trades Replay Attribution Rewire V1

## Objetivo

Esta branch religa o observation replay e a paper closed trades attribution ao contrato read-only de closed trades paper.

O gargalo anterior era prático:

- `replay_trade_count=0`
- `closed_trade_count=0`
- `attributed_trade_count=0`

A causa era ausência de fonte normalizada de closed trades paper e ausência de join key estável entre paper trades e observation records. O contrato `paper_closed_trades_readonly_source_contract_v1` passa a ser a entrada canônica read-only para replay e attribution.

## Fonte Canônica

O replay e a attribution aceitam:

```powershell
--closed-trades-source-contract data/reports/paper_closed_trades_readonly_source_contract_v1.json
```

O contrato deve conter:

- `source_contract_status=ok`
- `selected_source_path`
- `normalized_closed_trade_count`
- `recommended_join_key`
- `normalized_rows_sample`

Se `normalized_rows_sample` não contém todas as linhas normalizadas, os módulos carregam read-only a fonte indicada em `selected_source_path` e normalizam novamente usando o mesmo contrato.

## Replay

Com runtime read explícito:

```powershell
python .\scripts\build_ocr_master_candle_shadow_observation_replay_v1.py --project-root . --allow-runtime-read --observation-design-report data/reports/ocr_master_candle_shadow_observation_design_v1.json --oos-report data/reports/ocr_master_candle_positive_rule_oos_validation_v1.json --closed-trades-source-contract data/reports/paper_closed_trades_readonly_source_contract_v1.json --write --json
```

O replay preserva `decision=MANTER_EM_RESEARCH` e pode produzir `replay_trade_count > 0` quando o contrato e os survivors estão disponíveis.

## Attribution

Com runtime read explícito:

```powershell
python .\scripts\build_paper_closed_trades_shadow_rule_attribution_v1.py --project-root . --allow-runtime-read --shadow-replay-report data/reports/ocr_master_candle_shadow_observation_replay_v1.json --closed-trades-source-contract data/reports/paper_closed_trades_readonly_source_contract_v1.json --write --json
```

A attribution usa `recommended_join_key` do contrato, preferencialmente `order_id`, e preserva a semântica research-only. Quando replay e contrato são válidos, deve produzir `attributed_trade_count > 0`.

## Garantias de Segurança

Mesmo com replay e attribution materializados, os relatórios continuam sem autoridade operacional:

- `decision=MANTER_EM_RESEARCH`
- `research_only=true`
- `read_only=true`
- `paper_only=true`
- `shadow_only=true`
- `operational_authority=false`
- `paper_observation_allowed=false`
- `ready_for_shadow_observation=false`
- `can_apply_to_freqtrade=false`
- `can_apply_to_risk_manager=false`
- `can_promote_rules=false`
- `can_promote_model=false`
- `sends_orders=false`
- `changes_risk=false`
- `writes_runtime=false`
- `writes_sqlite=false`
- `writes_parquet=false`

## Fora de Escopo

Esta branch não:

- ativa paper observer;
- libera readiness;
- promove survivors;
- aplica regra;
- registra regra operacional;
- altera runtime;
- altera Freqtrade;
- altera RiskManager;
- altera Qlib runtime;
- altera IA Shadow runtime;
- altera modelos;
- altera configs;
- altera registry;
- altera sinais ativos;
- envia ordens;
- acessa exchange privada;
- escreve `data/runtime`;
- escreve SQLite;
- escreve Parquet operacional.
