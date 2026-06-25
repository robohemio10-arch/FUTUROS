# Daily Pattern Mining Research V1

## Objetivo

Esta branch cria a camada diaria de pattern mining research do SMART FUTUROS.
Ela minera padroes descritivos em memoria a partir de catalogos de
mistakes/winners, feature rows e trades ja fornecidos pelo chamador.

O resultado e pesquisa bloqueada por padrao. Patterns encontrados nao sao regras
candidatas, nao autorizam operacao e nao alteram nenhum componente de execucao.

## Relacao Com Branches 01-07

- Branch 01 consolidou o closeout de divergencia Paper/Master.
- Branch 02 definiu contratos e source map.
- Branch 03 criou loaders read-only.
- Branch 04 criou KPI pack diario.
- Branch 05 criou alinhamento Paper/Master.
- Branch 06 criou coverage e entry features.
- Branch 07 criou o catalogo diario de mistakes e winners.
- Esta Branch 08 usa entradas em memoria para minerar padroes descritivos.

## Catalogacao vs Pattern Mining

Catalogacao classifica trades como `winner`, `mistake`, `neutral` ou
`insufficient_evidence`.

Pattern mining agrega essas classificacoes com buckets de features para achar
concentracoes descritivas. O objetivo e explicar o que apareceu na amostra, nao
criar regra de trading.

## Pattern Research vs Candidate Rules

Pattern research:

- mede suporte, confianca, baseline, lift e coverage;
- mostra exemplos amostrais;
- exige validacao futura;
- permanece `status=blocked`.

Candidate rules ficam para branch futura com registry proprio, revisao manual e
validacao fora da amostra. Esta branch nao registra, promove ou executa regras.

## Entradas Em Memoria

`catalog_entry` esperado:

- `trade_id`
- `classification`
- `subclassification`
- `severity`
- `confidence`
- `evidence`
- `symbol`
- `side`
- flags de seguranca da entrada

`feature_row` esperado:

- `trade_id`
- `symbol`
- `side`
- `entry_time`
- `rsi_14`
- `dist_sma_20_pct`
- `pre_entry_volatility_20`
- `lb_5m_ret_close`
- `lb_10m_ret_close`
- `lb_30m_ret_close`
- `lb_5m_coverage_ratio`
- `lb_10m_coverage_ratio`
- `lb_30m_coverage_ratio`
- `has_entry_candle`
- `max_lookback_covered`

O CLI nao carrega fontes reais por padrao. Se nenhum input em memoria for
fornecido, o relatorio usa `input_mode=no_runtime_rows_loaded`.

## Buckets

Buckets suportados:

- RSI: `rsi_low`, `rsi_mid`, `rsi_high`, `rsi_extreme`.
- Distancia da media: `below_sma`, `near_sma`, `above_sma`.
- Momentum 10m: `lb_10m_negative`, `lb_10m_neutral`, `lb_10m_positive`.
- Momentum 30m: `lb_30m_negative`, `lb_30m_neutral`, `lb_30m_positive`.
- Volatilidade: `vol_low`, `vol_mid`, `vol_high`.
- Side: `side_long`, `side_short`, `side_unknown`.
- Symbol: `symbol_<SYMBOL>`.
- Subclassificacao: `sub_<VALUE>`.
- Severidade: `severity_<VALUE>`.

## Metricas

- `support_count`: quantas entradas satisfazem as condicoes.
- `target_count`: quantas entradas no suporte tambem pertencem ao alvo.
- `confidence`: `target_count / support_count`.
- `baseline_rate`: frequencia do alvo na amostra total.
- `lift`: `confidence / baseline_rate`.
- `coverage_pct`: percentual da amostra coberto pelo pattern.

## Por Que Nao Le Dados Reais Por Padrao

Esta branch e research-only e deve ser reprodutivel em testes unitarios sem
estado externo. Leitura de bases reais, artefatos runtime ou fontes operacionais
fica fora do escopo para evitar acoplamento com ambiente local e para preservar a
separacao entre evidencia de pesquisa e autorizacao operacional.

## Por Que Patterns Nao Liberam Operacao

Patterns podem ser espurios, pequenos ou dependentes do periodo analisado. Por
isso:

- `operational_authority=false`;
- `creates_candidate_rules=false`;
- `registers_candidate_rules=false`;
- `runs_oos_validation=false`;
- `promotion_allowed=false`;
- `requires_oos_validation=true` em cada pattern.

## Status Bloqueado

O relatorio sempre retorna:

- `status=blocked`
- `decision=MANTER_EM_RESEARCH`
- `reason=pattern_mining_research_only_without_operational_authority`

## Safety Flags

Flags preservadas:

- `research_only=true`
- `paper_only=true`
- `shadow_only=true`
- `read_only=true`
- `operational_authority=false`
- `live_trading_enabled=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `exchange_private_access=false`
- `sends_orders=false`
- `changes_risk=false`
- `changes_model=false`
- `updates_freqtrade=false`
- `updates_risk_manager=false`
- `updates_qlib_runtime=false`
- `updates_ai_shadow_runtime=false`
- `writes_runtime=false`
- `writes_data=false`
- `writes_sqlite=false`
- `writes_parquet=false`
- `runs_training=false`
- `runs_ocr=false`
- `runs_ai_shadow_incremental=false`

## Proximos Passos Permitidos

- Criar candidate shadow rule registry em branch futura.
- Criar validacao fora da amostra em branch futura.
- Criar AI Shadow feedback bridge em branch futura.
- Criar Qlib research dataset em branch futura.
- Criar daily learning orchestrator em branch futura.

## Acoes Proibidas

- Alterar Freqtrade.
- Alterar RiskManager.
- Alterar Qlib runtime.
- Alterar IA Shadow runtime.
- Alterar modelos.
- Alterar datasets.
- Habilitar live.
- Habilitar canary.
- Enviar ordem real.
- Usar exchange privada.
- Escrever artefatos em escopos runtime do projeto.
- Usar pattern mining para liberar operacao.
- Promover regra candidata.
- Promover modelo.
- Registrar candidate rules nesta branch.
- Rodar validacao fora da amostra nesta branch.
- Gerar codigo operacional de veto.

## CLI

Execucao sem escrita:

```powershell
python .\scripts\build_daily_pattern_mining_research_v1.py --project-root . --no-write --json
```

Escrita explicita somente fora de escopos runtime/dados:

```powershell
python .\scripts\build_daily_pattern_mining_research_v1.py --project-root . --output .\tmp\daily_pattern_mining_research_v1.json --json
```

Parametros:

- `--project-root`
- `--json`
- `--no-write`
- `--output`
- `--min-support-count`
- `--min-confidence`

## Validacao

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest .\tests\test_daily_pattern_mining_research_v1.py -q
python .\scripts\build_daily_pattern_mining_research_v1.py --project-root . --no-write --json
python .\scripts\generate_project_manifest.py
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r ".\requirements-dev.lock" --progress-spinner off
git diff --check
git status -sb
git status --short
```
