# Paper-Only Candidate Strategy AB Test V1

## Objetivo

Esta etapa cria um teste AB interventivo apenas para paper/candidate. O filtro
candidate bloqueia os dois cohorts ETHUSDT ruins identificados pela remediação:

- `ETHUSDT long`
- `ETHUSDT short`

O teste compara baseline vs candidate usando closed trades atribuídos e gera um
relatório local. Esta branch não integra o filtro ao Freqtrade, não ativa live,
não ativa canary e não envia ordens.

## Filtro

`PaperOnlyCandidateDecisionFilter` recebe uma proposta de trade e retorna:

- `BLOCK` para `symbol_norm=ETHUSDT`, `side_norm=long`
- `BLOCK` para `symbol_norm=ETHUSDT`, `side_norm=short`
- `ALLOW` para BTCUSDT long/short e demais símbolos/sides

Motivos estruturados:

- `discarded_negative_survivor_ethusdt_long`
- `discarded_negative_survivor_ethusdt_short`
- `candidate_filter_allow`

## Fontes

Com `--allow-runtime-read`, a CLI lê somente relatórios locais:

- `data/reports/paper_closed_trades_shadow_rule_attribution_v1.json`
- `data/reports/paper_shadow_observation_daily_impact_report_v1.json`
- `data/reports/paper_shadow_survivor_remediation_research_v1.json`

Se a attribution completa não estiver materializada, o AB test usa agregados do
impact/remediation report para calcular os totais dos 484 trades atribuídos e
mantém `decision_log_sample` agregado.

## Outputs

Com `--write`, a CLI escreve apenas:

- `data/reports/paper_only_candidate_strategy_ab_test_v1.json`
- `data/reports/paper_only_candidate_strategy_ab_test_v1.md`

Esses arquivos são runtime reports e não devem ser versionados.

## Comandos

Default seguro:

```powershell
python .\scripts\run_paper_only_candidate_strategy_ab_test_v1.py --project-root . --no-write --json
```

Execução paper/candidate explícita:

```powershell
python .\scripts\run_paper_only_candidate_strategy_ab_test_v1.py --project-root . --allow-runtime-read --write --json
```

Validação:

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests\test_paper_only_candidate_strategy_ab_test_v1.py -q
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
```

## Métricas

O relatório expõe:

- `baseline_trade_count`
- `candidate_trade_count`
- `blocked_trade_count`
- `allowed_trade_count`
- `blocked_eth_long_count`
- `blocked_eth_short_count`
- `baseline_net_pnl`
- `candidate_allowed_net_pnl`
- `blocked_net_pnl`
- `false_positive_reduction`
- `preserved_loss_count`
- `missed_opportunity_count`
- `candidate_vs_baseline_net_pnl_delta`
- `paper_behavior_changed`
- `live_behavior_changed`

## Segurança

O relatório preserva:

- `paper_only=true`
- `candidate_only=true`
- `live_behavior_changed=false`
- `live_release_allowed=false`
- `canary_release_allowed=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `sends_orders=false`
- `exchange_private_access=false`
- `changes_risk=false`
- `updates_freqtrade=false`
- `updates_risk_manager=false`
- `updates_qlib_runtime=false`
- `updates_ai_shadow_runtime=false`
- `writes_runtime=false`
- `writes_sqlite=false`
- `writes_parquet=false`

## Fora de escopo

Esta branch não cria adapter Freqtrade, não altera RiskManager, não altera Qlib,
não altera IA Shadow, não altera configs live e não envia ordens. Se o candidate
for aprovado para uma observação paper real, o adapter isolado deve ser uma
branch separada com testes próprios.
