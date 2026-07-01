# Paper Shadow Observation Daily Impact Report V1

## Objetivo

Este relatório transforma a attribution paper real em uma visão decisória research-only. Ele responde, sobre trades fechados atribuídos:

- quanto PnL teria sido permitido por `would_allow`;
- quanto PnL teria sido bloqueado por `would_block`;
- quantos false positives existem;
- quantas perdas foram preservadas;
- quantas oportunidades positivas foram perdidas;
- quais survivors devem ser descartados, revisados ou mantidos em observação passiva.

O relatório não é observer, não é veto e não é integração operacional.

## Entradas

Fontes padrão:

- `data/reports/paper_closed_trades_shadow_rule_attribution_v1.json`
- `data/reports/ocr_master_candle_shadow_observation_replay_v1.json`
- `data/reports/paper_closed_trades_readonly_source_contract_v1.json`

Sem `--allow-runtime-read`, nenhuma fonte runtime real é lida.

## Comandos

Bloqueio seguro padrão:

```powershell
python .\scripts\build_paper_shadow_observation_daily_impact_report_v1.py --project-root . --no-write --json
```

Leitura explícita e escrita de relatório research-only:

```powershell
python .\scripts\build_paper_shadow_observation_daily_impact_report_v1.py --project-root . --allow-runtime-read --paper-attribution-report data/reports/paper_closed_trades_shadow_rule_attribution_v1.json --shadow-replay-report data/reports/ocr_master_candle_shadow_observation_replay_v1.json --write --json
```

## Saídas

Com `--write`, a ferramenta escreve somente em `data/reports`:

- `data/reports/paper_shadow_observation_daily_impact_report_v1.json`
- `data/reports/paper_shadow_observation_daily_impact_report_v1.md`

Esses arquivos são runtime reports e não devem ser versionados.

## Métricas

O JSON contém:

- `total_closed_trades`
- `attributed_trade_count`
- `unattributed_trade_count`
- `would_allow_count`
- `would_block_count`
- `allowed_net_pnl`
- `blocked_net_pnl`
- `baseline_net_pnl`
- `false_positive_count`
- `false_positive_net_pnl`
- `preserved_loss_count`
- `preserved_loss_net_pnl`
- `missed_opportunity_count`
- `missed_opportunity_net_pnl`
- `expected_value_delta_total`
- `expected_value_delta_mean`
- `allowed_profit_factor`
- `blocked_profit_factor`
- `allowed_win_rate`
- `blocked_win_rate`
- `daily_breakdown`
- `symbol_side_breakdown`
- `survivor_rule_breakdown`
- `worst_survivors`
- `best_survivors`
- `survivor_recommendations`

## Classificações

- `false_positive`: `would_allow=true` e `pnl < 0`
- `true_positive_allow`: `would_allow=true` e `pnl >= 0`
- `preserved_loss`: `would_block=true` e `pnl < 0`
- `missed_opportunity`: `would_block=true` e `pnl >= 0`

## Recomendações Research-Only

- `DISCARD_RESEARCH_ONLY`: survivor com PnL negativo e alta incidência de false positives.
- `REVIEW_RESEARCH_ONLY`: amostra baixa ou resultado ambíguo.
- `KEEP_PASSIVE_OBSERVATION_ONLY`: PnL positivo, Profit Factor acima de 1.2 e false positive ratio aceitável.

Nenhuma recomendação ativa observer, altera risco, altera runtime ou promove regra.

## Garantias de Segurança

O relatório sempre preserva:

- `decision=MANTER_EM_RESEARCH`
- `paper_observation_allowed=false`
- `ready_for_shadow_observation=false`
- `operational_authority=false`
- `can_apply_to_freqtrade=false`
- `can_apply_to_risk_manager=false`
- `can_promote_rules=false`
- `can_promote_model=false`
- `sends_orders=false`
- `changes_risk=false`
- `writes_runtime=false`
- `writes_sqlite=false`
- `writes_parquet=false`
- `registers_shadow_rules=false`
- `applies_shadow_rules=false`
- `updates_freqtrade=false`
- `updates_risk_manager=false`
- `updates_qlib_runtime=false`
- `updates_ai_shadow_runtime=false`

## Fora de Escopo

Esta branch não altera:

- docker-compose;
- Dockerfiles;
- Freqtrade;
- RiskManager;
- Qlib runtime;
- IA Shadow runtime;
- model registry;
- active signals;
- `data/runtime`;
- config;
- `.env`;
- YAML;
- qualquer lógica live/canary/order.
