# OCR Master Candle Shadow Observation Replay V1

## Objetivo

Esta branch cria um replay observacional shadow, research-only e read-only para
aplicar hipoteticamente survivors OOS sobre historico fechado. O objetivo e
medir attribution descritiva de cohorts `would_allow` e `would_block`, sem
executar regra e sem alterar qualquer superficie operacional.

## Fonte Conceitual

A branch anterior de design definiu o contrato de survivors OOS:

- `survivor_rule_id`
- `survivor_expression`
- `would_allow`
- `would_block`
- `opportunity_score`
- `expected_value_delta`

Esta branch usa esse contrato como entrada opcional e mede o que teria acontecido
em trades historicos fechados.

## Modo Seguro Por Padrao

Sem `--allow-runtime-read`, o CLI nao carrega fontes reais e retorna:

- `status=blocked`
- `decision=MANTER_EM_RESEARCH`
- `input_mode=no_runtime_rows_loaded`
- `write_performed=false`

Isso impede acoplamento involuntario com runtime, paper, registros operacionais
ou fontes locais.

## Leitura Explicita

Com `--allow-runtime-read`, o operador deve informar explicitamente:

- `--observation-design-report` ou `--oos-report`
- `--trades-master`

O replay aceita fontes simples de pesquisa em JSON ou CSV. Arquivos operacionais
binarios ou fontes nao suportadas retornam erro estruturado, sem fallback
silencioso.

## Escrita

Nada e escrito por padrao.

Com `--write`, o unico destino permitido e um relatorio JSON research-only em
`data/reports`. A branch nunca escreve runtime, SQLite, parquet operacional,
registry, modelos, sinais ou configs.

## Semantica

`would_allow` significa apenas que uma linha historica fechada pertenceu ao
cohort observacional de um survivor.

`would_block` significa apenas que uma linha historica fechada ficou fora do
cohort observacional.

Nenhuma saida pode ser usada como:

- sinal;
- permissao de trade;
- veto runtime;
- regra ativa;
- alteracao de risco;
- autorizacao de live ou canary.

## Metricas

O replay calcula:

- `replay_trade_count`
- `would_allow_count`
- `would_block_count`
- `would_allow_net_pnl`
- `would_allow_profit_factor`
- `would_allow_win_rate`
- `baseline_net_pnl`
- `baseline_profit_factor`
- `baseline_win_rate`
- `expected_value_delta_total`
- `expected_value_delta_mean`
- `missed_opportunity_count`
- `preserved_loss_count`
- `false_positive_observation_count`
- `survivor_attribution_table`

## Safety Flags

O relatorio preserva:

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
- `registers_shadow_rules=false`
- `applies_shadow_rules=false`
- `updates_freqtrade=false`
- `updates_risk_manager=false`
- `updates_qlib_runtime=false`
- `updates_ai_shadow_runtime=false`
- `sends_orders=false`
- `changes_risk=false`
- `changes_model=false`
- `exchange_private_access=false`
- `writes_runtime=false`
- `writes_sqlite=false`
- `writes_parquet=false`

## Comandos

Modo padrao sem leitura e sem escrita:

```powershell
python .\scripts\build_ocr_master_candle_shadow_observation_replay_v1.py --project-root . --no-write --json
```

Replay com leitura explicita:

```powershell
python .\scripts\build_ocr_master_candle_shadow_observation_replay_v1.py --project-root . --allow-runtime-read --observation-design-report .\data\reports\ocr_master_candle_shadow_observation_design_v1.json --trades-master .\data\reports\closed_trades_fixture.json --no-write --json
```

Escrita explicita de relatorio research-only:

```powershell
python .\scripts\build_ocr_master_candle_shadow_observation_replay_v1.py --project-root . --allow-runtime-read --observation-design-report .\data\reports\ocr_master_candle_shadow_observation_design_v1.json --trades-master .\data\reports\closed_trades_fixture.json --write --json
```

## Validacao

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests\test_ocr_master_candle_shadow_observation_replay_v1.py -q
python .\scripts\build_ocr_master_candle_shadow_observation_replay_v1.py --project-root . --no-write --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r ".\requirements-dev.lock" --progress-spinner off
git diff --check
git status --short
```

## Garantias De Escopo

Esta branch nao altera:

- Docker ou compose;
- Freqtrade;
- RiskManager;
- Qlib runtime;
- IA Shadow runtime;
- model registry;
- active signals;
- `data/trades`;
- `data/features`;
- `data/runtime`;
- configs;
- `.env`;
- YAML;
- logica live, canary ou order.
